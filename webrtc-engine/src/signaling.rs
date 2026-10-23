use crate::{EngineError, SessionDescription, SessionToken};
use rcgen::generate_simple_self_signed;
use rustls::pki_types::PrivatePkcs8KeyDer;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::net::{IpAddr, SocketAddr};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;

const MAX_SIGNAL_BYTES: u64 = 256 * 1024;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct PairingEndpoint {
    pub address: SocketAddr,
    pub certificate_sha256: String,
    #[cfg(test)]
    #[serde(skip)]
    certificate: rustls::pki_types::CertificateDer<'static>,
}

pub struct PairingServer {
    listener: TcpListener,
    tls: TlsAcceptor,
}

#[derive(Deserialize)]
struct PairingHello {
    token: String,
}

#[derive(Serialize)]
struct PairingOffer<'a> {
    offer: &'a str,
}

#[derive(Deserialize)]
struct PairingAnswer {
    answer: String,
    accepted: bool,
}

impl PairingServer {
    pub async fn bind(address: IpAddr) -> Result<(Self, PairingEndpoint), EngineError> {
        if address.is_unspecified() {
            return Err(EngineError::UnsafeBindAddress);
        }
        let listener = TcpListener::bind(SocketAddr::new(address, 0))
            .await
            .map_err(signaling_error)?;
        let certified = generate_simple_self_signed(vec!["control-tv.local".into()])
            .map_err(signaling_error)?;
        let certificate = certified.cert.der().clone();
        let fingerprint = Sha256::digest(certificate.as_ref());
        let key = PrivatePkcs8KeyDer::from(certified.key_pair.serialize_der());
        let tls = rustls::ServerConfig::builder()
            .with_no_client_auth()
            .with_single_cert(vec![certificate.clone()], key.into())
            .map_err(signaling_error)?;
        let endpoint = PairingEndpoint {
            address: listener.local_addr().map_err(signaling_error)?,
            certificate_sha256: hex_fingerprint(&fingerprint),
            #[cfg(test)]
            certificate,
        };
        Ok((
            Self {
                listener,
                tls: TlsAcceptor::from(Arc::new(tls)),
            },
            endpoint,
        ))
    }

    pub async fn accept_once(
        self,
        token: &SessionToken,
        offer: &SessionDescription,
        deadline: Duration,
    ) -> Result<SessionDescription, EngineError> {
        tokio::time::timeout(deadline, self.exchange(token, offer))
            .await
            .map_err(|_| EngineError::NegotiationTimeout)?
    }

    async fn exchange(
        self,
        token: &SessionToken,
        offer: &SessionDescription,
    ) -> Result<SessionDescription, EngineError> {
        let (stream, _) = self.listener.accept().await.map_err(signaling_error)?;
        let stream = self.tls.accept(stream).await.map_err(signaling_error)?;
        let (reader, mut writer) = tokio::io::split(stream);
        let mut reader = BufReader::new(reader).take(MAX_SIGNAL_BYTES);
        let mut line = String::new();
        reader.read_line(&mut line).await.map_err(signaling_error)?;
        let hello: PairingHello = serde_json::from_str(&line).map_err(signaling_error)?;
        if !token.matches(&hello.token) {
            return Err(EngineError::AuthenticationFailed);
        }
        let encoded =
            serde_json::to_vec(&PairingOffer { offer: &offer.sdp }).map_err(signaling_error)?;
        writer.write_all(&encoded).await.map_err(signaling_error)?;
        writer.write_all(b"\n").await.map_err(signaling_error)?;
        line.clear();
        reader.read_line(&mut line).await.map_err(signaling_error)?;
        let answer: PairingAnswer = serde_json::from_str(&line).map_err(signaling_error)?;
        if !answer.accepted {
            return Err(EngineError::AuthenticationFailed);
        }
        Ok(SessionDescription { sdp: answer.answer })
    }
}

fn hex_fingerprint(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|byte| format!("{byte:02X}"))
        .collect::<Vec<_>>()
        .join(":")
}

fn signaling_error(error: impl std::fmt::Display) -> EngineError {
    EngineError::Transport(format!("signaling: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
    use tokio::net::TcpStream;
    use tokio_rustls::TlsConnector;

    #[tokio::test]
    async fn authenticated_loopback_pairing_exchanges_offer_and_answer_once() {
        let token = SessionToken::generate();
        let exposed = token.expose_for_pairing().to_owned();
        let (server, endpoint) = PairingServer::bind(IpAddr::V4(std::net::Ipv4Addr::LOCALHOST))
            .await
            .expect("bind");
        let address = endpoint.address;
        let client = tokio::spawn(async move {
            let stream = TcpStream::connect(endpoint.address).await.expect("connect");
            let mut roots = rustls::RootCertStore::empty();
            roots
                .add(endpoint.certificate)
                .expect("trusted session certificate");
            let config = rustls::ClientConfig::builder()
                .with_root_certificates(roots)
                .with_no_client_auth();
            let name =
                rustls::pki_types::ServerName::try_from("control-tv.local").expect("server name");
            let stream = TlsConnector::from(Arc::new(config))
                .connect(name, stream)
                .await
                .expect("TLS connect");
            let (reader, mut writer) = tokio::io::split(stream);
            writer
                .write_all(format!("{{\"token\":\"{exposed}\"}}\n").as_bytes())
                .await
                .expect("hello");
            let mut reader = BufReader::new(reader);
            let mut offer = String::new();
            reader.read_line(&mut offer).await.expect("offer");
            assert!(offer.contains("offer-sdp"));
            writer
                .write_all(b"{\"accepted\":true,\"answer\":\"answer-sdp\"}\n")
                .await
                .expect("answer");
        });
        let answer = server
            .accept_once(
                &token,
                &SessionDescription {
                    sdp: "offer-sdp".into(),
                },
                Duration::from_secs(2),
            )
            .await
            .expect("exchange");
        assert_eq!(answer.sdp, "answer-sdp");
        client.await.expect("client task");
        assert!(TcpStream::connect(address).await.is_err());
    }

    #[tokio::test]
    async fn endpoint_exposes_only_a_pinned_certificate_fingerprint() {
        let (_server, endpoint) = PairingServer::bind(IpAddr::V4(std::net::Ipv4Addr::LOCALHOST))
            .await
            .expect("bind");
        assert_eq!(endpoint.certificate_sha256.len(), 95);
        assert_eq!(endpoint.certificate_sha256.matches(':').count(), 31);
        let json = serde_json::to_string(&endpoint).expect("serialize endpoint");
        assert!(json.contains(&endpoint.certificate_sha256));
        assert!(!json.contains("PRIVATE"));
    }

    #[tokio::test]
    async fn untrusted_session_certificate_is_rejected_before_authentication() {
        let (server, endpoint) = PairingServer::bind(IpAddr::V4(std::net::Ipv4Addr::LOCALHOST))
            .await
            .expect("bind");
        let token = SessionToken::generate();
        let server_task = tokio::spawn(async move {
            server
                .accept_once(
                    &token,
                    &SessionDescription { sdp: "v=0".into() },
                    Duration::from_secs(1),
                )
                .await
        });
        let stream = TcpStream::connect(endpoint.address).await.expect("connect");
        let config = rustls::ClientConfig::builder()
            .with_root_certificates(rustls::RootCertStore::empty())
            .with_no_client_auth();
        let name =
            rustls::pki_types::ServerName::try_from("control-tv.local").expect("server name");
        assert!(TlsConnector::from(Arc::new(config))
            .connect(name, stream)
            .await
            .is_err());
        assert!(server_task.await.expect("server task").is_err());
    }

    #[tokio::test]
    async fn wildcard_listener_is_never_created() {
        assert!(matches!(
            PairingServer::bind(IpAddr::V4(std::net::Ipv4Addr::UNSPECIFIED)).await,
            Err(EngineError::UnsafeBindAddress)
        ));
    }
}
