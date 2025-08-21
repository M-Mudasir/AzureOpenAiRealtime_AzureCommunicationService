FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

RUN apk add --no-cache gcc libpq libpq-dev musl-dev supervisor

COPY ./requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt && \
	mkdir -p /tmp

COPY ./gunicorn.conf.py /app/gunicorn.conf.py
COPY ./app /app/app
COPY ./supervisord.conf /app/supervisord.conf

CMD ["/bin/sh", "-c", "supervisord -c /app/supervisord.conf"]
