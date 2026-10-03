# Stage 1: build the static files with Node.
FROM node:24-alpine AS build
WORKDIR /app

# Install dependencies first, so this layer stays cached when only code changes.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Stage 2: serve the built files with nginx. Node is not in the final image.
FROM nginx:1.30-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
