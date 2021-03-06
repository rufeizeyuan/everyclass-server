import datetime
import time
import uuid
from typing import overload, Union, Dict, Optional

from everyclass.server.db.postgres import pg_conn_context
from everyclass.server.db.redis import redis, redis_prefix


def insert_calendar_token(resource_type: str, semester: str, identifier: str) -> str:
    """
    生成日历令牌，写入数据库并返回字符串类型的令牌。此时的 last_used_time 是 NULL。

    :param resource_type: student/teacher
    :param semester: 学期字符串
    :param identifier: 学号或教工号
    :return: token 字符串
    """
    token = uuid.uuid4()

    with pg_conn_context() as conn, conn.cursor() as cursor:
        insert_query = """
        INSERT INTO calendar_tokens (type, identifier, semester, token, create_time)
            VALUES (%s,%s,%s,%s,%s);
        """
        cursor.execute(insert_query, (resource_type, identifier, semester, token, datetime.datetime.now()))
        conn.commit()
    return str(token)


def update_last_used_time(token: str):
    """更新token最后使用时间"""
    with pg_conn_context() as conn, conn.cursor() as cursor:
        insert_query = """
        UPDATE calendar_tokens SET last_used_time = %s WHERE token = %s;
        """
        cursor.execute(insert_query, (datetime.datetime.now(), uuid.UUID(token)))
        conn.commit()


def reset_tokens(student_id: str, typ: Optional[str] = "student") -> None:
    """删除某用户所有的 token，默认为学生"""
    with pg_conn_context() as conn, conn.cursor() as cursor:
        insert_query = """
        DELETE FROM calendar_tokens WHERE identifier = %s AND type = %s;
        """
        cursor.execute(insert_query, (student_id, typ))
        conn.commit()


def _parse(result):
    return {"type": result[0],
            "identifier": result[1],
            "semester": result[2],
            "token": result[3]}


@overload  # noqa: F811
def find_calendar_token(token: str) -> Union[Dict, None]:
    ...


@overload  # noqa: F811
def find_calendar_token(tid: str, semester: str) -> Union[Dict, None]:
    ...


@overload  # noqa: F811
def find_calendar_token(sid: str, semester: str) -> Union[Dict, None]:
    ...


def find_calendar_token(tid=None, sid=None, semester=None, token=None):
    """通过 token 或者 sid/tid + 学期获得 token 文档"""
    with pg_conn_context() as conn, conn.cursor() as cursor:
        if token:
            select_query = """
            SELECT type, identifier, semester, token, create_time, last_used_time FROM calendar_tokens
                WHERE token=%s
            """
            cursor.execute(select_query, (uuid.UUID(token),))
            result = cursor.fetchall()
            return _parse(result[0]) if result else None
        elif (tid or sid) and semester:
            select_query = """
            SELECT type, identifier, semester, token, create_time, last_used_time FROM calendar_tokens
                WHERE type=%s AND identifier=%s AND semester=%s;
            """
            cursor.execute(select_query, ("teacher" if tid else "student", tid, semester))
            result = cursor.fetchall()
            return _parse(result[0]) if result else None
        else:
            raise ValueError("tid/sid together with semester or token must be given to search a token document")


def init_table() -> None:
    with pg_conn_context() as conn, conn.cursor() as cursor:
        create_type_query = """
        DO $$ BEGIN
            CREATE TYPE people_type AS enum('student', 'teacher');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
        cursor.execute(create_type_query)

        create_table_query = """
        CREATE TABLE IF NOT EXISTS calendar_tokens
            (
                "type" people_type NOT NULL,
                identifier character varying(15) NOT NULL,
                semester character varying(15) NOT NULL,
                token uuid NOT NULL,
                create_time  timestamp with time zone NOT NULL,
                last_used_time  timestamp with time zone
            )
            WITH (
                OIDS = FALSE
            );
        """
        cursor.execute(create_table_query)

        create_index_query = """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_token
            ON calendar_tokens USING btree(token);
        """
        cursor.execute(create_index_query)

        create_index_query2 = """
        CREATE INDEX IF NOT EXISTS idx_type_idt_sem
            ON calendar_tokens USING btree("type", identifier, semester);
        """
        cursor.execute(create_index_query2)

        conn.commit()


SECONDS_IN_HOUR = 60 * 60


def use_cache(filename: str) -> bool:
    """
    漏桶算法决定是否使用缓存好了的文件。目前规则为一小时内第三次刷新则不使用缓存

    :param filename: ics文件名
    :return: 表示是否使用缓存的布尔值
    """

    def set_current():
        redis.set(key, f"{str(int(time.time()))},2")  # 一次过期

    key = f"{redis_prefix}:cal_tkn:{filename}"
    r = redis.get(key)
    if not r:
        set_current()
        return True

    timestamp, times = map(int, r.decode().split(","))

    if int(time.time()) - timestamp < SECONDS_IN_HOUR:  # 周期内，根据次数判断
        if times - 1 == 0:
            # 周期内缓存次数用完
            set_current()
            return False
        else:
            # 缓存次数没用完
            redis.set(key, f"{str(timestamp)},{str(times - 1)}")
            return True
    else:
        # 超过周期，可使用缓存
        return True
