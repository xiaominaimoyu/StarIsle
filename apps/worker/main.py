"""worker 容器入口(仅用于镜像内编排):对公网无监听。"""

from app.worker_runner import main

if __name__ == "__main__":
    main()