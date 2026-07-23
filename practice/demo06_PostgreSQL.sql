CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1) 建练习表：集合 UUID / BOOLEAN / JSONB / CHECK / TIMESTAMPTZ
DROP TABLE IF EXISTS demo_members;
CREATE TABLE demo_members
(
    id         UUID PRIMARY KEY     DEFAULT uuid_generate_v4(),
    username   VARCHAR(64) NOT NULL UNIQUE,
    role       VARCHAR(16) NOT NULL CHECK (role IN ('student', 'teacher', 'admin')),
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    profile    JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- 触发器函数：每次更新行时，把 updated_at 设为当前时间
CREATE OR REPLACE FUNCTION demo_touch_updated_at()
    RETURNS TRIGGER AS
$$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 把函数挂到表上：每行 UPDATE 之前自动执行
CREATE TRIGGER trg_demo_updated_at
    BEFORE UPDATE
    ON demo_members
    FOR EACH ROW
EXECUTE FUNCTION demo_touch_updated_at();


INSERT INTO demo_members (username, role, profile)
VALUES ('alice', 'student', '{
  "city": "上海",
  "tags": [
    "java",
    "spring"
  ]
}')
RETURNING id, username;


SELECT username,
       profile ->> 'city' AS city, -- 取文本
       profile -> 'tags'  AS tags  -- 取 JSON 数组
FROM demo_members
WHERE profile ->> 'city' = '上海'; -- 还能直接按 JSON 内部的值过滤


INSERT INTO demo_members (username, role)
VALUES ('alice', 'teacher') -- alice 已存在（username 唯一）
ON CONFLICT (username) DO UPDATE
    SET role = EXCLUDED.role -- EXCLUDED 代表「本次想插入的那行」
RETURNING username, role;



UPDATE demo_members
SET is_active = false
WHERE username = 'alice';