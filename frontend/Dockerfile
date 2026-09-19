# The UI's Vite dev server (design §2). Production hosting is out of the demo's scope.
FROM node:22-alpine

WORKDIR /app

# Dependencies first, so a source change does not reinstall them.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

# `docker compose` mounts ./frontend/src over /app/src; everything else comes from the image.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
