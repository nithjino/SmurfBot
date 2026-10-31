FROM python:3.15.2-alpine3.23 AS build
ENV UV_PYTHON_DOWNLOADS=never
RUN apk add --no-cache uv
WORKDIR /opt/smurfbot
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-cache --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev --no-cache --no-editable

FROM python:3.15.2-alpine3.23
ENV TZ=America/New_York \
    PATH="/opt/smurfbot/.venv/bin:${PATH}" \
    SMURFBOT_DATA_DIR="/home/smurfbot/data"
RUN apk add --no-cache tzdata \
    && adduser -h /home/smurfbot -D smurfbot \
    && cp /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo "${TZ}" > /etc/timezone
COPY --from=build /opt/smurfbot/.venv /opt/smurfbot/.venv
USER smurfbot
WORKDIR /home/smurfbot
RUN mkdir -p data/tags data/reminders
CMD ["smurfbot"]
