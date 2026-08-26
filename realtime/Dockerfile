FROM node:26-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json tsdown.config.ts ./
COPY src ./src
RUN npm run build


FROM node:26-alpine AS deps-prod

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev


FROM node:26-alpine AS runtime

RUN addgroup -S appgroup && adduser -S appuser -G appgroup
WORKDIR /app

COPY --from=deps-prod --chown=appuser:appgroup /app/node_modules ./node_modules
COPY --from=builder --chown=appuser:appgroup /app/dist ./dist
COPY --chown=appuser:appgroup package.json ./

USER appuser

EXPOSE 3000

CMD ["node", "dist/index.mjs"]
