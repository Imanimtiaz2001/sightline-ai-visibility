/** Correlation ids shared by HTTP headers and error reports. */

export function newId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().replace(/-/g, '')
    : Math.random().toString(16).slice(2) + Date.now().toString(16);
}

let session: string | null = null;

/** One id per browser tab, sent as `X-Client-Session` so all of a user's requests can be grouped. */
export function sessionId(): string {
  if (session) return session;
  try {
    session = sessionStorage.getItem('aiv.session') ?? newId();
    sessionStorage.setItem('aiv.session', session);
  } catch {
    session = newId();
  }
  return session;
}
