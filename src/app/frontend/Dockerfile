FROM node:22-alpine AS builder
WORKDIR /app

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/templates/default.conf.template

ENV PORT=8080
ENV API_ORIGIN=http://api:8080
EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
