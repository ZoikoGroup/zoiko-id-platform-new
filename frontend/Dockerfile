# Zoiko iD web app (Next.js standalone, DEC-H1). Build context: frontend/.
#   docker build -t zid/web .                     # the console (apps/web)
#   docker build --build-arg APP=operator -t zid/operator .
# Versions are pinned and dependencies come only from package-lock.json (npm ci) for reproducible builds (DEC-G4).
ARG NODE_IMAGE=node:22-bookworm-slim

FROM ${NODE_IMAGE} AS deps
WORKDIR /repo
COPY package.json package-lock.json turbo.json tsconfig.base.json ./
COPY packages ./packages
COPY apps ./apps
RUN npm ci --ignore-scripts

FROM deps AS build
ARG APP=web
ENV NEXT_TELEMETRY_DISABLED=1
RUN mkdir -p apps/${APP}/public && npm run build -w @zoiko/${APP}

FROM ${NODE_IMAGE} AS runtime
ARG APP=web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0 APP_DIR=apps/${APP}
RUN groupadd --system --gid 10001 zid && useradd --system --uid 10001 --gid zid --no-create-home zid
WORKDIR /srv
# Standalone output keeps the monorepo layout: <root>/apps/<app>/server.js
COPY --from=build --chown=zid:zid /repo/apps/${APP}/.next/standalone ./
COPY --from=build --chown=zid:zid /repo/apps/${APP}/.next/static ./apps/${APP}/.next/static
COPY --from=build --chown=zid:zid /repo/apps/${APP}/public ./apps/${APP}/public
USER zid
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+process.env.PORT+'/').then(r=>process.exit(r.status<500?0:1)).catch(()=>process.exit(1))"
CMD ["sh", "-c", "exec node ${APP_DIR}/server.js"]
