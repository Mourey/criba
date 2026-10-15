# Criba web: build the Vite app with Node, serve static files with nginx (ADR 0024).
# Build context is ./src/web; the nginx config comes from the `nginx_conf` additional
# context (./src/docker/web), set in docker-compose.yml.
FROM node:26.11.1-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.30.5-alpine
COPY --from=nginx_conf default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
