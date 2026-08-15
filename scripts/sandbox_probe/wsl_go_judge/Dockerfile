# WSL / 本地预习：统一 base + pin 的 go-judge v1.12.2（须带 seccomp build tag）
# 正式选型仍须在目标 K8s 节点重跑探针。
FROM harbor.deepfos.com/base/python3.11.9:debian12

WORKDIR /opt
COPY go-judge /opt/go-judge
COPY mount.yaml /opt/mount.yaml

# pydata Profile：与 scripts/sandbox_venv/requirements.txt 对齐；NumPy 必须保持 1.x
# （放在 seccomp COPY 之前，避免调 filter 时重复装包）
RUN chmod +x /opt/go-judge \
    && python3 -m pip install --no-cache-dir \
         -i https://pypi.tuna.tsinghua.edu.cn/simple \
         --trusted-host pypi.tuna.tsinghua.edu.cn \
         "numpy>=1.26,<2" pandas \
    && python3 -c "import numpy, pandas; assert numpy.__version__.startswith('1.'); print(numpy.__version__, pandas.__version__)"

COPY seccomp.yaml /opt/seccomp.yaml

EXPOSE 5050/tcp
ENTRYPOINT [ \
  "/opt/go-judge", \
  "-http-addr=0.0.0.0:5050", \
  "-mount-conf=/opt/mount.yaml", \
  "-seccomp-conf=/opt/seccomp.yaml" \
]
