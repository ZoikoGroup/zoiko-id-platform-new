# Device cookie: note for the privacy owner

Status: for review by the DPO and Legal. This note describes what the software does. It does not state a legal basis or a legal conclusion.

## What it is

The web app sets one first-party cookie, named `__Host-zid_device` (`zid_device` on plain http for local development). It holds a random 128-bit id, written as 32 lowercase hex characters. It is generated on the web server the first time a browser makes a request, and it holds no name, email address, account id or other personal data.

Attributes: HttpOnly (page scripts cannot read it), Secure, SameSite=Lax, Path=/, no Domain, lifetime one year (Max-Age 31536000 seconds).

## Why

The backend counts sign-up, verification and company-claim attempts per contact, per network and per device. The device count helps spot repeated or automated abuse that changes email addresses or networks. It is used for fraud and abuse prevention only. It is not used for advertising, analytics or profiling across sites.

## How it travels and what is stored

- The browser sends the cookie only to the web app. The web server copies the id into an internal request header, `x-zid-device`, on calls to the backend. The cookie itself is never sent to the backend.
- The web server always discards any `x-zid-device` header a browser sends, so a client cannot choose its own id.
- The backend stores only a keyed hash of the id, not the id itself. The id is not written to logs by the web app.

## Classification and notice

The team's proposal treats this as a strictly-necessary-for-security cookie. Whether that classification holds, which legal basis applies, whether consent or a notice is needed, and the wording of any cookie or privacy notice are decisions for the DPO and Legal. Until they decide, this document records only the technical facts above.

## How to turn it off

Set the environment variable `ZID_DEVICE_COOKIE=off` for the web app and restart it. The default is on (unset or any other value). When off, the web app does not set the cookie, does not send `x-zid-device`, and still removes any such header a client sends. The backend then sees an empty device value and counts only by contact and network. A cookie already in a browser stays there until it expires or the person clears it, but it is no longer read.
