FROM python:3.11.11

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    libglfw3 \
    libglew2.2 \
    libgl1-mesa-glx \
    libglib2.0-0 \
    wget \
    && apt-get clean && rm -rf /var/lib/apt/lists/*


WORKDIR /app

COPY . .

RUN pip install .

RUN pip install dm_control