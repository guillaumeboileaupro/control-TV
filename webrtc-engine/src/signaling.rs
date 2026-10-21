use crate::{EngineError, SessionDescription, SessionToken};
use serde::{Deserialize, Serialize};
use std::net::{IpAddr, SocketAddr};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;

const MAX_SIGNAL_BYTES: u64 = 256 * 1024;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct PairingEndpoint {
    pub address: SocketAddr,
}

pub struct PairingServer {
    listener: TcpListener,
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
        let endpoint = PairingEndpoint {
            address: listener.local_addr().map_err(signaling_error)?,
        };
        Ok((Self { listener }, endpoint))
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
        let (reader, mut writer) = stream.into_split();
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

fn signaling_error(error: impl std::fmt::Display) -> EngineError {
    EngineError::Transport(format!("signaling: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
    use tokio::net::TcpStream;

    #[tokio::test]
    async fn authenticated_loopback_pairing_exchanges_offer_and_answer_once() {
        let token = SessionToken::generate();
        let exposed = token.expose_for_pairing().to_owned();
        let (server, endpoint) = PairingServer::bind(IpAddr::V4(std::net::Ipv4Addr::LOCALHOST))
            .await
            .expect("bind");
        let client = tokio::spawn(async move {
            let stream = TcpStream::connect(endpoint.address).await.expect("connect");
            let (reader, mut writer) = stream.into_split();
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
    }

    #[tokio::test]
    async fn wildcard_listener_is_never_created() {
        assert!(matches!(
            PairingServer::bind(IpAddr::V4(std::net::Ipv4Addr::UNSPECIFIED)).await,
            Err(EngineError::UnsafeBindAddress)
        ));
    }
}
