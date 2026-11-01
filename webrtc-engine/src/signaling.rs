use crate::{EngineError, SessionDescription, SessionToken};
use rcgen::generate_simple_self_signed;
use rustls::pki_types::PrivatePkcs8KeyDer;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::net::{IpAddr, SocketAddr};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{
    AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader, ReadHalf, Take, WriteHalf,
};
use tokio::net::{TcpListener, TcpStream};
use tokio::task::JoinSet;
use tokio_rustls::server::TlsStream;
use tokio_rustls::TlsAcceptor;

const MAX_SIGNAL_BYTES: u64 = 256 * 1024;
/// A connection must finish TLS and present its token within this time, so a silent one cannot
/// hold a slot until the negotiation deadline.
const HELLO_TIMEOUT: Duration = Duration::from_secs(5);
/// Connections authenticated at the same time; further ones are closed at once.
const MAX_PENDING_HELLOS: usize = 8;

type SignalReader = Take<BufReader<ReadHalf<TlsStream<TcpStream>>>>;
type SignalWriter = WriteHalf<TlsStream<TcpStream>>;

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

#[cfg(test)]
impl PairingEndpoint {
    pub(crate) fn certificate_for_tests(&self) -> rustls::pki_types::CertificateDer<'static> {
        self.certificate.clone()
    }
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

    /// The first connection that presents the session token gets the offer. A stray, aborted,
    /// silent or wrongly authenticated connection is closed without ending the session, so a
    /// LAN scan or a receiver's failed first attempt cannot cancel it; the token stays single-use:
    /// once a connection presented it, any later failure of that exchange ends the negotiation.
    async fn exchange(
        self,
        token: &SessionToken,
        offer: &SessionDescription,
    ) -> Result<SessionDescription, EngineError> {
        let mut pending = JoinSet::new();
        let (mut reader, mut writer) = loop {
            tokio::select! {
                accepted = self.listener.accept() => {
                    let (stream, _) = accepted.map_err(signaling_error)?;
                    if pending.len() < MAX_PENDING_HELLOS {
                        let (tls, token) = (self.tls.clone(), token.clone());
                        pending.spawn(tokio::time::timeout(
                            HELLO_TIMEOUT,
                            authenticate(tls, stream, token),
                        ));
                    }
                }
                Some(done) = pending.join_next() => {
                    if let Ok(Ok(Ok(Some(channel)))) = done {
                        break channel;
                    }
                }
            }
        };
        // Dropping the set closes every other pending connection.
        drop(pending);
        let encoded =
            serde_json::to_vec(&PairingOffer { offer: &offer.sdp }).map_err(signaling_error)?;
        writer.write_all(&encoded).await.map_err(signaling_error)?;
        writer.write_all(b"\n").await.map_err(signaling_error)?;
        let mut line = String::new();
        reader.read_line(&mut line).await.map_err(signaling_error)?;
        let answer: PairingAnswer = serde_json::from_str(&line).map_err(signaling_error)?;
        if !answer.accepted {
            return Err(EngineError::AuthenticationFailed);
        }
        Ok(SessionDescription { sdp: answer.answer })
    }
}

/// The connection's channel once it completed TLS and presented `token`; `None` for another token.
async fn authenticate(
    tls: TlsAcceptor,
    stream: TcpStream,
    token: SessionToken,
) -> Result<Option<(SignalReader, SignalWriter)>, EngineError> {
    let stream = tls.accept(stream).await.map_err(signaling_error)?;
    let (reader, writer) = tokio::io::split(stream);
    let mut reader = BufReader::new(reader).take(MAX_SIGNAL_BYTES);
    let mut line = String::new();
    reader.read_line(&mut line).await.map_err(signaling_error)?;
    let hello: PairingHello = serde_json::from_str(&line).map_err(signaling_error)?;
    Ok(token.matches(&hello.token).then_some((reader, writer)))
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

    /// A receiver that trusts the session certificate, sends `token` and, given an answer,
    /// returns it after reading the offer; without one it closes once the offer arrived.
    async fn receiver(endpoint: PairingEndpoint, token: String, answer: Option<&'static str>) {
        let stream = TcpStream::connect(endpoint.address).await.expect("connect");
        let mut roots = rustls::RootCertStore::empty();
        roots
            .add(endpoint.certificate)
            .expect("trusted session certificate");
        let config = rustls::ClientConfig::builder()
            .with_root_certificates(roots)
            .with_no_client_auth();
        let name = rustls::pki_types::ServerName::try_from("control-tv.local").expect("name");
        let stream = TlsConnector::from(Arc::new(config))
            .connect(name, stream)
            .await
            .expect("TLS connect");
        let (reader, mut writer) = tokio::io::split(stream);
        writer
            .write_all(format!("{{\"token\":\"{token}\"}}\n").as_bytes())
            .await
            .expect("hello");
        let mut reader = BufReader::new(reader);
        let mut offer = String::new();
        let _ = reader.read_line(&mut offer).await;
        if let Some(answer) = answer {
            assert!(offer.contains("offer-sdp"));
            writer.write_all(answer.as_bytes()).await.expect("answer");
            writer.write_all(b"\n").await.expect("newline");
        }
    }

    const ANSWER: &str = "{\"accepted\":true,\"answer\":\"answer-sdp\"}";

    async fn serve(
        server: PairingServer,
        token: SessionToken,
    ) -> tokio::task::JoinHandle<Result<SessionDescription, EngineError>> {
        tokio::spawn(async move {
            server
                .accept_once(
                    &token,
                    &SessionDescription {
                        sdp: "offer-sdp".into(),
                    },
                    Duration::from_secs(4),
                )
                .await
        })
    }

    #[tokio::test]
    async fn stray_and_wrongly_authenticated_connections_do_not_end_the_session() {
        let token = SessionToken::generate();
        let (server, endpoint) = PairingServer::bind(IpAddr::V4(std::net::Ipv4Addr::LOCALHOST))
            .await
            .expect("bind");
        let session = serve(server, token.clone()).await;

        // A port scan, then plain-text garbage, then a TLS client with another token.
        drop(TcpStream::connect(endpoint.address).await.expect("scan"));
        let mut garbage = TcpStream::connect(endpoint.address).await.expect("garbage");
        garbage
            .write_all(b"GET / HTTP/1.0\r\n\r\n")
            .await
            .expect("write");
        receiver(endpoint.clone(), "x".repeat(48), None).await;
        // A silent connection that stays open must not delay the real receiver.
        let _silent = TcpStream::connect(endpoint.address).await.expect("silent");

        let started = std::time::Instant::now();
        receiver(
            endpoint,
            token.expose_for_pairing().to_owned(),
            Some(ANSWER),
        )
        .await;
        let answer = session.await.expect("server task").expect("exchange");
        assert_eq!(answer.sdp, "answer-sdp");
        assert!(
            started.elapsed() < HELLO_TIMEOUT,
            "the silent connection blocked pairing"
        );
    }

    #[tokio::test]
    async fn the_token_is_single_use_once_presented() {
        let token = SessionToken::generate();
        let (server, endpoint) = PairingServer::bind(IpAddr::V4(std::net::Ipv4Addr::LOCALHOST))
            .await
            .expect("bind");
        let session = serve(server, token.clone()).await;
        // Authenticated, then gone without an answer: the negotiation ends now, it does not wait
        // for another holder of the same token.
        receiver(endpoint, token.expose_for_pairing().to_owned(), None).await;
        let result = tokio::time::timeout(Duration::from_secs(2), session)
            .await
            .expect("ends before the negotiation deadline")
            .expect("server task");
        assert!(
            matches!(result, Err(EngineError::Transport(_))),
            "{result:?}"
        );
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
