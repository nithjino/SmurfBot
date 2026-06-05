FROM python:3.14.5-alpine3.23
ENV TZ=America/New_York \
    PATH="/home/smurfbot/.venv/bin:${PATH}"
RUN apk add --no-cache tzdata uv \
    && adduser -h /home/smurfbot -D smurfbot \
    && cp /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo "${TZ}" > /etc/timezone
USER smurfbot
WORKDIR /home/smurfbot
COPY --chown=smurfbot:smurfbot pyproject.toml .
COPY --chown=smurfbot:smurfbot uv.lock .
RUN uv sync --frozen --no-dev --no-cache
RUN mkdir -p app/src app/logs app/src/tags/files app/src/reminders/files
COPY --chown=smurfbot:smurfbot src/ app/src/
WORKDIR /home/smurfbot/app
CMD ["python","src/start.py", "-c", "config.ini"]
