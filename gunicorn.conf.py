bind = "0.0.0.0:80"
workers = 8
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 2000
max_requests = 10000
max_requests_jitter = 1000
preload_app = True
forwarded_allow_ips = "*"

raw_env = ["UVICORN_CMD_ARGS=--proxy-headers"]
