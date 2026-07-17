FROM node:24.14.1-alpine3.23 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.29.5-alpine3.23
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
