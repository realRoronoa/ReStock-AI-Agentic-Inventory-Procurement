# ReStock AI frontend.
#
# NOT VERIFIED — the Docker daemon was unavailable where this was written, so
# this image has never been built.

FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src

# VITE_ variables are baked in at build time and are PUBLIC. Only the backend
# URL belongs here.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

FROM nginx:alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
