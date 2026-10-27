export interface RequestTimers {
  set(callback: () => void, delayMs: number): number;
  clear(handle: number): void;
}

export interface RequestTimeoutFailure {
  code: "bridge_timeout";
  message: string;
}

// A lost host reply must not leave the window permanently busy. This is deliberately only a
// caller-side deadline: it never retries or resends the operation, whose delivery is ambiguous.
export async function settleWithin<T>(
  operation: Promise<T>,
  delayMs: number,
  timers: RequestTimers,
): Promise<T> {
  let timer = 0;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = timers.set(() => {
      const failure: RequestTimeoutFailure = {
        code: "bridge_timeout",
        message: "The application host did not return the command result before its deadline.",
      };
      reject(failure);
    }, delayMs);
  });
  try {
    return await Promise.race([operation, timeout]);
  } finally {
    timers.clear(timer);
  }
}
