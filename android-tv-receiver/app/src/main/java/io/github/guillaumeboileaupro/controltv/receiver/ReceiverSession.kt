package io.github.guillaumeboileaupro.controltv.receiver

import android.content.Context
import org.webrtc.AudioSource
import org.webrtc.AudioTrack
import org.webrtc.DefaultVideoDecoderFactory
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.MediaStream
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpReceiver
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.SurfaceViewRenderer
import org.webrtc.VideoTrack
import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.Closeable
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetSocketAddress
import java.net.Socket
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.X509TrustManager

private const val NEGOTIATION_DEADLINE_MS = 20_000L
private const val DISCONNECT_GRACE_MS = 3_000L

internal class OneShotGate {
    private val claimed = AtomicBoolean(false)
    fun claim(): Boolean = claimed.compareAndSet(false, true)
    fun close() { claimed.set(true) }
}

internal class DisconnectRecoveryGate {
    private val generation = AtomicLong(0)
    fun disconnected(): Long = generation.incrementAndGet()
    fun recovered() { generation.incrementAndGet() }
    fun isStillDisconnected(ticket: Long): Boolean = generation.get() == ticket
}

internal class ReceiverSession(
    context: Context,
    private val renderer: SurfaceViewRenderer,
    private val onState: (String) -> Unit,
    private val onRetryReady: () -> Unit,
) : Closeable {
    private val egl = EglBase.create()
    private val factory: PeerConnectionFactory
    private var peer: PeerConnection? = null
    private var socket: Socket? = null
    private var answerWriter: BufferedWriter? = null
    private val answerGate = IceCompleteAnswerGate()
    private val closed = AtomicBoolean(false)
    private val retryNotification = OneShotGate()
    private val negotiationDeadline = OneShotGate()
    private val disconnectRecovery = DisconnectRecoveryGate()

    init {
        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions.builder(context.applicationContext).createInitializationOptions()
        )
        factory = PeerConnectionFactory.builder()
            .setVideoDecoderFactory(DefaultVideoDecoderFactory(egl.eglBaseContext))
            .createPeerConnectionFactory()
        renderer.init(egl.eglBaseContext, null)
        renderer.setEnableHardwareScaler(true)
    }

    fun connect(target: PairingTarget) {
        check(peer == null) { "A session is already active" }
        Thread({ pair(target) }, "control-tv-pairing").start()
        Thread({
            Thread.sleep(NEGOTIATION_DEADLINE_MS)
            if (negotiationDeadline.claim()) {
                onState("Erreur : délai de négociation dépassé")
                close()
                notifyRetryReady()
            }
        }, "control-tv-negotiation-watchdog").apply { isDaemon = true }.start()
    }

    private fun pair(target: PairingTarget) {
        try {
            onState("Connexion…")
            val plain = Socket().also { it.connect(InetSocketAddress(target.host, target.port), 5_000) }
            val context = SSLContext.getInstance("TLS").apply {
                init(null, arrayOf(PinnedCertificateTrustManager(target.certificateSha256)), null)
            }
            val connection = (context.socketFactory.createSocket(
                plain,
                target.host,
                target.port,
                true,
            ) as SSLSocket).also {
                it.enabledProtocols = it.supportedProtocols.filter { protocol ->
                    protocol == "TLSv1.3" || protocol == "TLSv1.2"
                }.toTypedArray()
                it.soTimeout = 10_000
                socket = it
                it.startHandshake()
            }
            val writer = BufferedWriter(OutputStreamWriter(connection.getOutputStream(), Charsets.UTF_8))
            answerWriter = writer
            val reader = BufferedReader(InputStreamReader(connection.getInputStream(), Charsets.UTF_8))
            writer.write(PairingProtocol.hello(target)); writer.newLine(); writer.flush()
            val remoteOffer = PairingProtocol.offer(reader.readLine() ?: error("Sender closed signaling"))
            onState("Négociation…")
            val currentPeer = createPeer()
            peer = currentPeer
            currentPeer.setRemoteDescription(settingObserver {
                currentPeer.createAnswer(creationObserver { answer ->
                    currentPeer.setLocalDescription(settingObserver {
                        answerGate.markLocalDescriptionSet()
                        sendLocalAnswer()
                    }, answer)
                }, MediaConstraints())
            }, SessionDescription(SessionDescription.Type.OFFER, remoteOffer))
        } catch (error: Exception) {
            onState("Erreur : ${error.message ?: error.javaClass.simpleName}")
            close()
            notifyRetryReady()
        }
    }

    private fun createPeer(): PeerConnection = requireNotNull(factory.createPeerConnection(
        PeerConnection.RTCConfiguration(emptyList()),
        object : PeerConnection.Observer {
            override fun onIceConnectionChange(state: PeerConnection.IceConnectionState) {
                onState(if (state == PeerConnection.IceConnectionState.CONNECTED) "Diffusion" else state.name.lowercase())
            }
            override fun onTrack(transceiver: org.webrtc.RtpTransceiver) {
                when (val track = transceiver.receiver.track()) {
                    is VideoTrack -> track.addSink(renderer)
                    is AudioTrack -> track.setEnabled(true)
                }
            }
            override fun onSignalingChange(state: PeerConnection.SignalingState) = Unit
            override fun onIceConnectionReceivingChange(receiving: Boolean) = Unit
            override fun onIceGatheringChange(state: PeerConnection.IceGatheringState) {
                if (state == PeerConnection.IceGatheringState.COMPLETE) {
                    answerGate.markIceComplete()
                    sendLocalAnswer()
                }
            }
            override fun onIceCandidate(candidate: IceCandidate) = Unit
            override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>) = Unit
            override fun onAddStream(stream: MediaStream) = Unit
            override fun onRemoveStream(stream: MediaStream) = Unit
            override fun onDataChannel(channel: org.webrtc.DataChannel) = Unit
            override fun onRenegotiationNeeded() = Unit
            override fun onAddTrack(receiver: RtpReceiver, streams: Array<out MediaStream>) = Unit
            override fun onConnectionChange(state: PeerConnection.PeerConnectionState) {
                when (state) {
                    PeerConnection.PeerConnectionState.CONNECTED -> disconnectRecovery.recovered()
                    PeerConnection.PeerConnectionState.DISCONNECTED -> {
                        val ticket = disconnectRecovery.disconnected()
                        Thread({
                            Thread.sleep(DISCONNECT_GRACE_MS)
                            if (disconnectRecovery.isStillDisconnected(ticket)) {
                                onState(state.name.lowercase())
                                close()
                                notifyRetryReady()
                            }
                        }, "control-tv-disconnect-watchdog").apply { isDaemon = true }.start()
                    }
                    PeerConnection.PeerConnectionState.FAILED -> {
                        disconnectRecovery.recovered()
                        onState(state.name.lowercase())
                        close()
                        notifyRetryReady()
                    }
                    else -> Unit
                }
            }
        }
    ))

    private fun creationObserver(onSuccess: (SessionDescription) -> Unit): SdpObserver = object : SdpObserver {
        override fun onCreateSuccess(description: SessionDescription) = onSuccess(description)
        override fun onSetSuccess() = Unit
        override fun onCreateFailure(message: String) = failSdp(message)
        override fun onSetFailure(message: String) = failSdp(message)
    }

    private fun settingObserver(onSuccess: () -> Unit): SdpObserver = object : SdpObserver {
        override fun onCreateSuccess(description: SessionDescription) = Unit
        override fun onSetSuccess() = onSuccess()
        override fun onCreateFailure(message: String) = failSdp(message)
        override fun onSetFailure(message: String) = failSdp(message)
    }

    private fun failSdp(message: String) {
        onState("Erreur SDP : $message")
        close()
        notifyRetryReady()
    }

    private fun notifyRetryReady() {
        if (retryNotification.claim()) onRetryReady()
    }

    @Synchronized
    private fun sendLocalAnswer() {
        val answer = answerGate.take(peer?.localDescription?.description) ?: return
        val writer = answerWriter
        if (writer == null) return
        runCatching {
            writer.write(PairingProtocol.answer(answer, true)); writer.newLine(); writer.flush()
            negotiationDeadline.close()
        }.onFailure {
            onState("Erreur : envoi de la réponse impossible")
            close()
            notifyRetryReady()
        }
    }

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        disconnectRecovery.recovered()
        negotiationDeadline.close()
        socket?.close(); socket = null
        answerWriter = null
        peer?.close(); peer?.dispose(); peer = null
        renderer.release()
        factory.dispose()
        egl.release()
    }
}

private class PinnedCertificateTrustManager(expectedFingerprint: String) : X509TrustManager {
    private val expected = expectedFingerprint.replace(":", "").uppercase()

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
        throw java.security.cert.CertificateException("Client certificates are not accepted")

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val certificate = chain?.singleOrNull()
            ?: throw java.security.cert.CertificateException("Expected one session certificate")
        val actual = MessageDigest.getInstance("SHA-256")
            .digest(certificate.encoded)
            .joinToString("") { byte -> "%02X".format(byte) }
        if (!MessageDigest.isEqual(actual.toByteArray(), expected.toByteArray())) {
            throw java.security.cert.CertificateException("Session certificate does not match")
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

