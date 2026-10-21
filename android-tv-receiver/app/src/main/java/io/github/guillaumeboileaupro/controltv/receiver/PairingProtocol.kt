package io.github.guillaumeboileaupro.controltv.receiver

import org.json.JSONObject

internal data class PairingTarget(val host: String, val port: Int, val token: String) {
    init {
        require(host.isNotBlank())
        require(port in 1..65535)
        require(token.length == 48 && token.all(Char::isLetterOrDigit))
    }
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

