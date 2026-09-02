# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- dev target
# Used by docker-compose for local work: source is bind-mounted, node_modules
# lives in a container volume so it never touches the host filesystem. That
# keeps the repo small and, more importantly, stops macOS-native binaries from
# a host `npm install` colliding with the Linux ones the container needs.
FROM node:22-alpine AS dev
WORKDIR /app
# Installs exactly what package-lock.json pins, so there is no silent version
# drift between your machine, CI and production. It fails loudly if the lockfile
# and package.json disagree, which is the behaviour you want from a build.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# --------------------------------------------------------------- build stage
FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------------------------------------------------------------- production
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
