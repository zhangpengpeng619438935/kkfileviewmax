-- =============================================================
-- kkFileView 服务化 · 统计与审计库 建表脚本 (MySQL 8.0)
-- 库名: kkfileview   用户: kkfileview
-- 执行: mysql -ukkfileview -p kkfileview < 001_schema.sql
-- =============================================================

CREATE DATABASE IF NOT EXISTS kkfileview
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE kkfileview;

-- -------------------------------------------------------------
-- 1. 调用方系统账号
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_account (
  id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
  app_id        VARCHAR(64)  NOT NULL COMMENT '调用方系统标识',
  app_name      VARCHAR(128) NOT NULL DEFAULT '' COMMENT '系统中文名',
  app_secret    VARCHAR(128) NOT NULL COMMENT '密钥(用于换 token 与签名)',
  ip_whitelist  VARCHAR(512) NOT NULL DEFAULT '' COMMENT 'IP 白名单,逗号分隔,空=不限制',
  qps_limit     INT UNSIGNED NOT NULL DEFAULT 60 COMMENT '该系统每秒限额',
  enabled       TINYINT(1)   NOT NULL DEFAULT 1,
  remark        VARCHAR(255) NOT NULL DEFAULT '',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_app_id (app_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='调用方系统账号';

-- -------------------------------------------------------------
-- 2. 请求明细(按天分区)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS access_log (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  request_id    VARCHAR(40)  NOT NULL COMMENT '链路追踪 ID',
  app_id        VARCHAR(64)  NOT NULL,
  emp_no        VARCHAR(32)  NOT NULL COMMENT '登录人工号',
  emp_name      VARCHAR(64)  NOT NULL DEFAULT '',
  dept          VARCHAR(64)  NOT NULL DEFAULT '',
  file_key      CHAR(64)     NOT NULL COMMENT '文件唯一键(url 归一化后 sha256)',
  file_name     VARCHAR(512) NOT NULL DEFAULT '',
  file_ext      VARCHAR(16)  NOT NULL DEFAULT '',
  file_type     VARCHAR(32)  NOT NULL DEFAULT '' COMMENT 'office/pdf/image/txt/cad/media/other',
  action        VARCHAR(32)  NOT NULL DEFAULT 'preview' COMMENT 'preview/task/status/token',
  cost_ms       INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '端到端耗时(毫秒)',
  e2e           TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '1=耗时已由预览页可见时刻回填(真实加载耗时)；0=只有接口耗时, 不计入延迟统计',
  upstream_ms   INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '引擎侧耗时',
  cache_hit     TINYINT(1)   NOT NULL DEFAULT 0,
  is_async      TINYINT(1)   NOT NULL DEFAULT 0,
  status_code   INT          NOT NULL DEFAULT 200,
  success       TINYINT(1)   NOT NULL DEFAULT 1,
  error_msg     VARCHAR(255) NOT NULL DEFAULT '',
  client_ip     VARCHAR(64)  NOT NULL DEFAULT '',
  user_agent    VARCHAR(255) NOT NULL DEFAULT '',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id, created_at),
  KEY idx_created    (created_at),
  KEY idx_app_time   (app_id, created_at),
  KEY idx_file_time  (file_key, created_at),
  KEY idx_emp_time   (emp_no, created_at),
  KEY idx_req        (request_id),
  KEY idx_cost       (created_at, cost_ms)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='请求明细(按天分区)'
PARTITION BY RANGE (TO_DAYS(created_at)) (
  PARTITION p_start VALUES LESS THAN (TO_DAYS('2026-10-19')),
  PARTITION pmax    VALUES LESS THAN MAXVALUE
);

-- -------------------------------------------------------------
-- 3. 审计日志(谁在什么时间从哪个系统看了哪个文件)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  request_id    VARCHAR(40)  NOT NULL,
  emp_no        VARCHAR(32)  NOT NULL,
  emp_name      VARCHAR(64)  NOT NULL DEFAULT '',
  dept          VARCHAR(64)  NOT NULL DEFAULT '',
  app_id        VARCHAR(64)  NOT NULL,
  file_key      CHAR(64)     NOT NULL,
  file_url      VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '原始文件地址',
  file_name     VARCHAR(512) NOT NULL DEFAULT '',
  file_ext      VARCHAR(16)  NOT NULL DEFAULT '',
  action        VARCHAR(32)  NOT NULL DEFAULT 'preview',
  result        VARCHAR(16)  NOT NULL DEFAULT 'success' COMMENT 'success/denied/error',
  detail        VARCHAR(255) NOT NULL DEFAULT '',
  client_ip     VARCHAR(64)  NOT NULL DEFAULT '',
  cost_ms       INT UNSIGNED NOT NULL DEFAULT 0,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id, created_at),
  KEY idx_emp_time  (emp_no, created_at),
  KEY idx_file_time (file_key, created_at),
  KEY idx_app_time  (app_id, created_at),
  KEY idx_created   (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='访问审计日志'
PARTITION BY RANGE (TO_DAYS(created_at)) (
  PARTITION p_start VALUES LESS THAN (TO_DAYS('2026-10-19')),
  PARTITION pmax    VALUES LESS THAN MAXVALUE
);

-- -------------------------------------------------------------
-- 4. 日聚合(按 app_id + 文件类型)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stat_daily (
  stat_date     DATE         NOT NULL,
  app_id        VARCHAR(64)  NOT NULL,
  file_type     VARCHAR(32)  NOT NULL DEFAULT 'all',
  total_cnt     BIGINT UNSIGNED NOT NULL DEFAULT 0,
  success_cnt   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  fail_cnt      BIGINT UNSIGNED NOT NULL DEFAULT 0,
  cache_hit_cnt BIGINT UNSIGNED NOT NULL DEFAULT 0,
  async_cnt     BIGINT UNSIGNED NOT NULL DEFAULT 0,
  sum_cost_ms   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  avg_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  max_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  min_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  uniq_emp      INT UNSIGNED NOT NULL DEFAULT 0,
  uniq_file     INT UNSIGNED NOT NULL DEFAULT 0,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (stat_date, app_id, file_type),
  KEY idx_date (stat_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='按日聚合';

-- -------------------------------------------------------------
-- 5. 周 / 月聚合(结构同日聚合)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stat_weekly (
  stat_week     VARCHAR(16)  NOT NULL COMMENT '如 2026-W42',
  week_start    DATE         NOT NULL,
  app_id        VARCHAR(64)  NOT NULL,
  file_type     VARCHAR(32)  NOT NULL DEFAULT 'all',
  total_cnt     BIGINT UNSIGNED NOT NULL DEFAULT 0,
  success_cnt   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  fail_cnt      BIGINT UNSIGNED NOT NULL DEFAULT 0,
  cache_hit_cnt BIGINT UNSIGNED NOT NULL DEFAULT 0,
  sum_cost_ms   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  avg_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  max_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  min_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  uniq_emp      INT UNSIGNED NOT NULL DEFAULT 0,
  uniq_file     INT UNSIGNED NOT NULL DEFAULT 0,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (stat_week, app_id, file_type),
  KEY idx_week (stat_week)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stat_monthly (
  stat_month    VARCHAR(8)   NOT NULL COMMENT '如 2026-10',
  app_id        VARCHAR(64)  NOT NULL,
  file_type     VARCHAR(32)  NOT NULL DEFAULT 'all',
  total_cnt     BIGINT UNSIGNED NOT NULL DEFAULT 0,
  success_cnt   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  fail_cnt      BIGINT UNSIGNED NOT NULL DEFAULT 0,
  cache_hit_cnt BIGINT UNSIGNED NOT NULL DEFAULT 0,
  sum_cost_ms   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  avg_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  max_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  min_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  uniq_emp      INT UNSIGNED NOT NULL DEFAULT 0,
  uniq_file     INT UNSIGNED NOT NULL DEFAULT 0,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (stat_month, app_id, file_type),
  KEY idx_month (stat_month)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------------
-- 6. 文件维度聚合(浏览了哪些文件)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stat_file_daily (
  stat_date     DATE         NOT NULL,
  file_key      CHAR(64)     NOT NULL,
  file_name     VARCHAR(512) NOT NULL DEFAULT '',
  file_ext      VARCHAR(16)  NOT NULL DEFAULT '',
  file_type     VARCHAR(32)  NOT NULL DEFAULT '',
  view_cnt      BIGINT UNSIGNED NOT NULL DEFAULT 0,
  uniq_emp      INT UNSIGNED NOT NULL DEFAULT 0,
  app_cnt       INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '有多少个系统访问过',
  sum_cost_ms   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  avg_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  max_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  min_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  last_view_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (stat_date, file_key),
  KEY idx_date_cnt (stat_date, view_cnt),
  KEY idx_file (file_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文件维度日聚合';

-- -------------------------------------------------------------
-- 7. 延迟直方图(用于估算 P50/P95/P99,桶内线性插值)
--    桶: 0-50 / 50-100 / 100-200 / 200-500 / 500-1000 /
--        1000-3000 / 3000-10000 / 10000+
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stat_latency_hist (
  stat_date     DATE         NOT NULL,
  app_id        VARCHAR(64)  NOT NULL,
  bucket_lo     INT UNSIGNED NOT NULL,
  bucket_hi     INT UNSIGNED NOT NULL,
  cnt           BIGINT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (stat_date, app_id, bucket_lo),
  KEY idx_date (stat_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='延迟分布直方图';

-- -------------------------------------------------------------
-- 8. 总累计(每个 app_id 一行,随写入递增)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stat_total (
  app_id        VARCHAR(64)  NOT NULL,
  total_cnt     BIGINT UNSIGNED NOT NULL DEFAULT 0,
  success_cnt   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  fail_cnt      BIGINT UNSIGNED NOT NULL DEFAULT 0,
  sum_cost_ms   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  max_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0,
  min_cost_ms   INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '0 表示尚无记录',
  first_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (app_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='总访问量累计';

-- -------------------------------------------------------------
-- 9. 转码任务(网关自建状态,引擎无状态查询接口)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS preview_task (
  task_id       VARCHAR(40)  NOT NULL,
  app_id        VARCHAR(64)  NOT NULL,
  emp_no        VARCHAR(32)  NOT NULL DEFAULT '',
  file_key      CHAR(64)     NOT NULL,
  file_url      VARCHAR(1024) NOT NULL,
  file_name     VARCHAR(512) NOT NULL DEFAULT '',
  status        VARCHAR(16)  NOT NULL DEFAULT 'queued' COMMENT 'queued/converting/ready/failed',
  err_msg       VARCHAR(255) NOT NULL DEFAULT '',
  retry_cnt     INT UNSIGNED NOT NULL DEFAULT 0,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  ready_at      DATETIME     NULL,
  PRIMARY KEY (task_id),
  KEY idx_file_status (file_key, status),
  KEY idx_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='转码任务';

-- -------------------------------------------------------------
-- 10. 分区滚动: 由外部脚本 tools/partition_roll.sh 每日执行(cron)
--     命令示例: ALTER TABLE access_log REORGANIZE PARTITION pmax INTO
--               (PARTITION p20261020 VALUES LESS THAN (TO_DAYS('2026-10-21')),
--                PARTITION pmax VALUES LESS THAN MAXVALUE)
--     说明: REORGANIZE PARTITION 在存储过程动态 SQL 中存在解析限制, 故放到脚本里做
-- -------------------------------------------------------------

-- -------------------------------------------------------------
-- 11. 明细清理
--     保留天数由网关的 RETENTION_DAYS 统一控制（默认 180 天，分批删不锁表），
--     所以这里不再用 MySQL 事件删 access_log / audit_log ——
--     以前 access_log 写死 30 天，会和 RETENTION_DAYS=180 打架（数据只能回溯 30 天）。
--     保留下面的老事件定义仅为兼容已部署环境，默认禁用。
-- -------------------------------------------------------------
DROP EVENT IF EXISTS ev_purge_access_log;
CREATE EVENT IF NOT EXISTS ev_purge_access_log
  ON SCHEDULE EVERY 1 DAY STARTS (CURRENT_DATE + INTERVAL 1 DAY + INTERVAL 3 HOUR)
  DISABLE
  DO DELETE FROM access_log WHERE created_at < NOW() - INTERVAL 30 DAY;

DROP EVENT IF EXISTS ev_purge_audit_log;
CREATE EVENT IF NOT EXISTS ev_purge_audit_log
  ON SCHEDULE EVERY 1 DAY STARTS (CURRENT_DATE + INTERVAL 1 DAY + INTERVAL 3 HOUR)
  DISABLE
  DO DELETE FROM audit_log WHERE created_at < NOW() - INTERVAL 180 DAY;

DROP EVENT IF EXISTS ev_purge_file_stat;
CREATE EVENT IF NOT EXISTS ev_purge_file_stat
  ON SCHEDULE EVERY 1 DAY STARTS (CURRENT_DATE + INTERVAL 1 DAY + INTERVAL 4 HOUR)
  DO DELETE FROM stat_file_daily WHERE stat_date < CURDATE() - INTERVAL 365 DAY;
