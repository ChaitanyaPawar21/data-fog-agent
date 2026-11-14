# Development: `next dev` with the source mounted (docker-compose.yml uses target: dev).
FROM node:24-alpine AS dev
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]

FROM dev AS build
# The /api rewrite in next.config.ts is fixed at build time. In production Caddy sends
# /api straight to the backend anyway, so this only matters if Caddy is left out.
ENV BACKEND_URL=http://backend:8000 NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Production: the standalone server only, without the full node_modules.
FROM node:24-alpine AS prod
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
