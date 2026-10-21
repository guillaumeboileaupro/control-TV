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
import java.util.concurrent.atomic.AtomicBoolean

internal class ReceiverSession(
    context: Context,
    private val renderer: SurfaceViewRenderer,
    private val onState: (String) -> Unit,
) : Closeable {
    private val egl = EglBase.create()
    private val factory: PeerConnectionFactory
    private var peer: PeerConnection? = null
    private var socket: Socket? = null
    private val closed = AtomicBoolean(false)

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
    }

    private fun pair(target: PairingTarget) {
        try {
            onState("Connexion…")
            val connection = Socket().also {
                it.connect(InetSocketAddress(target.host, target.port), 5_000)
                it.soTimeout = 10_000
                socket = it
            }
            val writer = BufferedWriter(OutputStreamWriter(connection.getOutputStream(), Charsets.UTF_8))
            val reader = BufferedReader(InputStreamReader(connection.getInputStream(), Charsets.UTF_8))
            writer.write(PairingProtocol.hello(target)); writer.newLine(); writer.flush()
            val remoteOffer = PairingProtocol.offer(reader.readLine() ?: error("Sender closed signaling"))
            onState("Négociation…")
            val currentPeer = createPeer()
            peer = currentPeer
            currentPeer.setRemoteDescription(observer {
                currentPeer.createAnswer(observer { answer ->
                    currentPeer.setLocalDescription(observer {
                        writer.write(PairingProtocol.answer(answer.description, true)); writer.newLine(); writer.flush()
                    }, answer)
                }, MediaConstraints())
            }, SessionDescription(SessionDescription.Type.OFFER, remoteOffer))
        } catch (error: Exception) {
            onState("Erreur : ${error.message ?: error.javaClass.simpleName}")
            close()
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
            override fun onIceGatheringChange(state: PeerConnection.IceGatheringState) = Unit
            override fun onIceCandidate(candidate: IceCandidate) = Unit
            override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>) = Unit
            override fun onAddStream(stream: MediaStream) = Unit
            override fun onRemoveStream(stream: MediaStream) = Unit
            override fun onDataChannel(channel: org.webrtc.DataChannel) = Unit
            override fun onRenegotiationNeeded() = Unit
            override fun onAddTrack(receiver: RtpReceiver, streams: Array<out MediaStream>) = Unit
            override fun onConnectionChange(state: PeerConnection.PeerConnectionState) {
                if (state == PeerConnection.PeerConnectionState.FAILED || state == PeerConnection.PeerConnectionState.CLOSED) onState(state.name.lowercase())
            }
        }
    ))

    private fun observer(onSuccess: (SessionDescription) -> Unit = {}): SdpObserver = object : SdpObserver {
        override fun onCreateSuccess(description: SessionDescription) = onSuccess(description)
        override fun onSetSuccess() = onSuccess(SessionDescription(SessionDescription.Type.ANSWER, ""))
        override fun onCreateFailure(message: String) = onState("Erreur SDP : $message")
        override fun onSetFailure(message: String) = onState("Erreur SDP : $message")
    }

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        socket?.close(); socket = null
        peer?.close(); peer?.dispose(); peer = null
        renderer.release()
        factory.dispose()
        egl.release()
    }
}

