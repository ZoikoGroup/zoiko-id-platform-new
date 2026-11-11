# Sender domain: DNS checklist for production mail

Owner of the decision: **Security + Brand** (Communication Estate §07, "Sending domain / branding": exact mail-from domain(s), display name, reply-to and SPF/DKIM/DMARC). Engineering does not choose the domain. This page is what the owner publishes and how to confirm it works. Local development uses a personal relay and is not a model for production (see `docs/phases/phase-k-communication-estate.md`).

## What must be true before production mail
| Record | Where | Requirement |
|---|---|---|
| SPF | TXT on the mail-from domain | exactly one `v=spf1` record, lists the sending provider, ends `-all` (soft `~all` only while rolling out), at most 10 DNS lookups, no `ptr` |
| DKIM | TXT on `<selector>._domainkey.<domain>` | provider's public key, `v=DKIM1`, non-empty `p=`, not marked `t=y` (test) |
| DMARC | TXT on `_dmarc.<domain>` | `v=DMARC1; p=quarantine` or `p=reject`, an `rua=` mailbox someone reads, `pct` 100; alignment `adkim`/`aspf` `s` (strict) when the provider allows |
| TLS | provider and receiver | the SMTP relay is used with TLS (`ZID_EMAIL_*` settings, password from a secret file, never in the repo) |
| Reports | `rua=` mailbox | monitored; bounces and complaints reach the suppression list (the platform already suppresses after a hard bounce) |

Example shape (placeholders, not Zoiko values):
```
mail.example.            TXT  "v=spf1 include:_spf.<provider> -all"
s1._domainkey.mail.example. TXT  "v=DKIM1; k=rsa; p=<public key from the provider>"
_dmarc.mail.example.     TXT  "v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@mail.example; adkim=s; aspf=s"
```
Start DMARC at `p=none` only to read reports for a short, dated period; move to `quarantine` then `reject` and record the dates.

## Check it
```
python3 tools/scripts/check_sender_domain.py mail.example --dkim-selector s1
```
Exit 0: nothing wrong (warnings are listed). Exit 1: at least one ERROR. Exit 2: DNS could not be read (a failure to read is never reported as "missing"). The script uses DNS-over-HTTPS with certificate checking on; run it from a machine whose Python trusts the usual certificate authorities.

## Then, in the cell's environment
1. `ZID_EMAIL_FROM="Zoiko iD <no-reply@mail.example>"` (display name **Zoiko iD**, not the development value).
2. `ZID_EMAIL_PUBLIC_BASE_URL` set to the public console origin (https, bare origin).
3. `ZID_EMAIL_ENABLED=true` and `ZID_EMAIL_ENABLED_TEMPLATES` listing only templates whose flows exist (`python -m zid_platform.modules.communication.cli coverage` shows which are wired).
4. Send one `[test]` message to a mailbox you own with the CLI `send-test`, and read the received headers: `spf=pass`, `dkim=pass`, `dmarc=pass`.
5. Record the sign-off (who approved the domain and the copy, and the date) in `docs/signoff/signup-owner-pack.md` item 4.

## Not engineering
Choosing and buying the domain, publishing the records, approving the copy, and the legal roles named in each notice (controller or processor) belong to Security, Brand, Legal and Privacy.
