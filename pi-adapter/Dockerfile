FROM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY tsconfig.json ./
COPY src ./src
RUN npm run check && mkdir -p /app/var && chown node:node /app/var
ENV NODE_ENV=production COMMERCE_PI_HOST=0.0.0.0 COMMERCE_PI_STATE_DIR=/app/var/sessions COMMERCE_PI_AGENT_DIR=/app/var/config
USER node
EXPOSE 58010
CMD ["node", "src/server.ts"]
