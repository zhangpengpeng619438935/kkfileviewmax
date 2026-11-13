# 构建 kkFileView 4.4.0 引擎镜像

官方在 GitHub/Gitee 只发**源码**，没有 Linux 二进制包（发行包在付费社区）。
所以这套镜像从源码构建，步骤如下（实测 10 分钟内完成）。

## 1. 取源码

GitHub 的仓库约 745MB，其中 ~700MB 是 Windows 版 `LibreOfficePortable`，构建用不到。
**从 Gitee 下载（5MB/s 左右）并排除它**：

```bash
mkdir -p /opt/kkfileview-service/build && cd /opt/kkfileview-service/build
curl -sL https://gitee.com/kekingcn/file-online-preview/archive/refs/tags/v4.4.0.tar.gz \
  | tar xz --exclude='*/server/LibreOfficePortable/*'
```

## 2. Maven 构建

4.4.0 仍是 **Java 8**（`<java.version>1.8</java.version>`），用 Maven 容器构建即可。
Maven Central 走阿里云镜像，否则每个包几十 KB/s 会很慢。

`build/maven-settings.xml`：

```xml
<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">
  <mirrors>
    <mirror>
      <id>aliyun-central</id>
      <url>https://maven.aliyun.com/repository/public</url>
      <mirrorOf>central</mirrorOf>
    </mirror>
  </mirrors>
</settings>
```

```bash
cd file-online-preview-v4.4.0
docker run --rm -v "$PWD":/src -v kk-m2:/root/.m2 \
  -v /opt/kkfileview-service/build/maven-settings.xml:/tmp/settings.xml:ro \
  -w /src docker.1ms.run/library/maven:3.9-eclipse-temurin-8 \
  mvn -B -s /tmp/settings.xml -pl server package -DskipTests
```

产物：

- `server/target/kkFileView-4.4.0.tar.gz`（bin/ + config/ + log/）
- `server/target/kkFileView-4.4.0.jar`（约 306MB）

解压到 `build/app440`，供 Dockerfile 使用：

```bash
mkdir -p /opt/kkfileview-service/build && \
  tar xzf server/target/kkFileView-4.4.0.tar.gz -C /opt/kkfileview-service/build && \
  mv /opt/kkfileview-service/build/kkFileView-4.4.0 /opt/kkfileview-service/build/app440
```

## 3. 构建镜像

```bash
cd /opt/kkfileview-service/build
docker build -f /opt/kkfileview-service/engine/Dockerfile.440 -t kkfileview-custom:4.4.0 .
```

镜像 = Ubuntu 22.04 + 从 `kkfileview-custom:4.1.0` COPY 的 JDK8/字体 + 4.4.0 程序
+ 外挂 LibreOffice 26.8（运行时由 compose 挂载）。

> 为什么用 22.04：LibreOffice 26.8 要求 glibc ≥ 2.34，20.04 只有 2.31。

## 4. 切换

```bash
# .env
KK_IMAGE=kkfileview-custom:4.4.0
```

compose 里引擎数据卷要指向新目录：

```yaml
- ./data/kkfileview:/opt/kkFileView-4.4.0/file
- ./data/lo26/opt/libreoffice26.8:/opt/libreoffice26.8:ro
```

```bash
docker compose up -d kkfileview
```

## 5. 验证

```bash
docker logs kkfileview 2>&1 | grep -E "Connected|Started process"
# Connected: 'socket,host=127.0.0.1,port=2001,tcpNoDelay=1'

curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9012/kk/
```

## 踩坑记录

1. **`找不到office组件`**：4.4.0 的 `ConfigUtils.getHomePath()` 依赖
   `KKFILEVIEW_BIN_FOLDER`，不设置就去 `<cwd>/server/src/main/config/...` 找配置，
   读不到 `office.home`。镜像里已加
   `ENV KKFILEVIEW_BIN_FOLDER=/opt/kkFileView-4.4.0/bin`。
2. **不要用 GitHub 的 tarball**：745MB 里 700MB 是 Windows LibreOfficePortable，
   而且 `git clone` 走 TLS 还容易断。
3. **Maven 一定要配镜像**：直连 Central 只有 30~50KB/s，配阿里云后 7MB/s。
