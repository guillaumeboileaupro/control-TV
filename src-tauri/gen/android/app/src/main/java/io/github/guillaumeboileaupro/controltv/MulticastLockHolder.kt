package io.github.guillaumeboileaupro.controltv

/** The part of `WifiManager.MulticastLock` the holder needs, so it can be tested on the JVM. */
interface MulticastLockApi {
    val isHeld: Boolean
    fun acquire()
    fun release()
}

/**
 * Holds Android's Wi-Fi multicast lock while the app is in the foreground.
 *
 * Without it many devices filter incoming multicast, so the mDNS replies of Cast
 * receivers never reach the embedded zeroconf. Acquiring and releasing are idempotent:
 * repeated foreground and background transitions never stack locks or release one that
 * is not held.
 */
class MulticastLockHolder(private val lock: MulticastLockApi) {
    val isHeld: Boolean
        get() = lock.isHeld

    fun acquire() {
        if (!lock.isHeld) {
            lock.acquire()
        }
    }

    fun release() {
        if (lock.isHeld) {
            lock.release()
        }
    }
}
