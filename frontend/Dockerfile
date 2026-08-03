FROM node:20-alpine AS build
WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

# Vite bakes VITE_* variables in at BUILD time, not runtime - and since API
# calls happen in the user's BROWSER, this must be a URL the browser can
# reach, never the backend's internal docker-network hostname. Defaults to
# the backend's published host port (docker-compose.yml maps it to
# localhost:8000) - override at build time (--build-arg) if the API is
# reachable elsewhere.
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
