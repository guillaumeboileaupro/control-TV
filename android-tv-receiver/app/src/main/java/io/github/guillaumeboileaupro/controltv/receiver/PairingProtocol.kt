package io.github.guillaumeboileaupro.controltv.receiver

import org.json.JSONObject

internal class PairingTarget(
    val host: String,
    val port: Int,
    val token: String,
    val certificateSha256: String,
) {
    init {
        require(host.isNotBlank())
        require(port in 1..65535)
        require(token.length == 48 && token.all(Char::isLetterOrDigit))
        require(certificateSha256.matches(Regex("(?i)[0-9a-f]{2}(:[0-9a-f]{2}){31}")))
    }

    override fun toString(): String = "PairingTarget(host=<redacted>, port=$port, credentials=<redacted>)"
}

internal object PairingProtocol {
    fun hello(target: PairingTarget): String = JSONObject().put("token", target.token).toString()

    fun offer(line: String): String = JSONObject(line).getString("offer").also {
        require(it.startsWith("v=0")) { "Invalid WebRTC offer" }
    }

    fun answer(sdp: String, accepted: Boolean): String = JSONObject()
        .put("accepted", accepted)
        .put("answer", if (accepted) sdp else "")
        .toString()
}

