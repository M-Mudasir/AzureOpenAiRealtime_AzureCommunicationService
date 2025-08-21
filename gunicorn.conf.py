
bind = "0.0.0.0:80"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
forwarded_allow_ips = "*"

raw_env = ["UVICORN_CMD_ARGS=--proxy-headers"]
