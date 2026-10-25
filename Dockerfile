FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/n13
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tools ./tools
RUN chmod -R a+rX /srv/n13/app /srv/n13/tools
RUN mkdir -p /srv/n13/data && useradd -u 10001 -m bot && chown -R bot:bot /srv/n13/data
USER bot
CMD ["python","-m","app.bot"]
