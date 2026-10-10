# tenant-console

Next.js App Router app. Routes under `src/app/(console)/` follow the UI/UX Wireframes spec navigation. Feature code lives in `src/features/<feature>/`.

Rules (ADR-0003): the BFF only forwards calls with the session's token; no authorization decisions, business logic or database access here. The backend re-authorizes every mutation.
