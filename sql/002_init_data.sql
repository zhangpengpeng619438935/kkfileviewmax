-- 初始化调用方账号(默认口令请部署后立即修改)
USE kkfileview;

INSERT INTO app_account (app_id, app_name, app_secret, ip_whitelist, qps_limit, remark)
VALUES
 ('kk-demo', '演示系统', 'your-app-secret', '', 60, '部署后请修改密钥'),
 ('oa', 'OA 门户', 'CHANGE-ME-oa', '', 60, '预留'),
 ('plm', 'PLM 系统', 'CHANGE-ME-plm', '', 60, '预留'),
 ('kb', '示例知识库', 'CHANGE-ME-kb', '', 60, '预留')
ON DUPLICATE KEY UPDATE app_name=VALUES(app_name);

-- 说明: 新增调用方
-- INSERT INTO app_account (app_id, app_name, app_secret, ip_whitelist, qps_limit)
-- VALUES ('新系统ID', '系统名', '密钥', '127.0.0.1,127.0.0.1', 60);
