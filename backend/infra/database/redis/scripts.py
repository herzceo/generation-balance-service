"""Lua sources. Amounts are integer micro-dollars; only ``RESERVE`` compares ``version``.

RESERVE          KEYS bal, gen, dirty, inflight
                 ARGV expected_version, user_id, dialog_id, model_name, plan_free, plan_bonus,
                      plan_paid, plan_free_requests, authorized_cost, started_at, ttl,
                      client_request_id, reservation_token
SETTLE           KEYS gen, bal, dirty, inflight
                 ARGV content, billed (charged), provider_billed (decimal string), ttl,
                      client_request_id, user_id, refund_free, refund_bonus, refund_paid,
                      refunded_surplus
REFUND           KEYS bal, gen, dirty, inflight
                 ARGV user_id, error, ttl, client_request_id, plan_free, plan_bonus, plan_paid,
                      plan_free_requests
TOP_UP           KEYS bal, topup, dirty
                 ARGV user_id, ttl, paid, bonus, free_requests
ADVANCE_VERSION  KEYS bal, dirty
                 ARGV expected_version, new_version, user_id
"""

RESERVE = """
local function neg(s) if s == '0' then return s end return '-' .. s end
if redis.call('EXISTS', KEYS[2]) == 1 then
  local r = redis.call('HMGET', KEYS[2], 'status', 'user_id', 'dialog_id', 'model_name',
    'plan_free_usd', 'plan_bonus_usd', 'plan_paid_usd', 'plan_free_requests',
    'authorized_cost_usd', 'started_at', 'content', 'billed_cost_usd',
    'provider_billed_cost_usd', 'refunded_surplus_usd', 'error', 'reservation_token')
  for i = 1, 16 do if not r[i] then r[i] = '' end end
  return {'DUP', unpack(r)}
end
if redis.call('EXISTS', KEYS[1]) == 0 then return {'MISSING'} end
local c = redis.call('HMGET', KEYS[1], 'version', 'free_usd', 'bonus_usd', 'paid_usd',
  'free_requests')
if c[1] ~= ARGV[1] then return {'CONFLICT', c[2], c[3], c[4], c[5], c[1]} end
redis.call('HINCRBY', KEYS[1], 'free_usd', neg(ARGV[5]))
redis.call('HINCRBY', KEYS[1], 'bonus_usd', neg(ARGV[6]))
redis.call('HINCRBY', KEYS[1], 'paid_usd', neg(ARGV[7]))
redis.call('HINCRBY', KEYS[1], 'free_requests', neg(ARGV[8]))
local v = redis.call('HINCRBY', KEYS[1], 'version', 1)
redis.call('HSET', KEYS[2], 'status', 'running', 'user_id', ARGV[2], 'dialog_id', ARGV[3],
  'model_name', ARGV[4], 'plan_free_usd', ARGV[5], 'plan_bonus_usd', ARGV[6],
  'plan_paid_usd', ARGV[7], 'plan_free_requests', ARGV[8], 'authorized_cost_usd', ARGV[9],
  'started_at', ARGV[10], 'reservation_token', ARGV[13])
redis.call('EXPIRE', KEYS[2], ARGV[11])
redis.call('ZADD', KEYS[4], ARGV[10], ARGV[12])
redis.call('SADD', KEYS[3], ARGV[2])
return {'OK', tostring(v)}
"""

SETTLE = """
local status = redis.call('HGET', KEYS[1], 'status')
if status == 'done' then return {'DONE'} end
if status ~= 'running' then return {'STALE'} end
if ARGV[7] ~= '0' or ARGV[8] ~= '0' or ARGV[9] ~= '0' then
  if redis.call('EXISTS', KEYS[2]) == 0 then return {'MISSING'} end
  redis.call('HINCRBY', KEYS[2], 'free_usd', ARGV[7])
  redis.call('HINCRBY', KEYS[2], 'bonus_usd', ARGV[8])
  redis.call('HINCRBY', KEYS[2], 'paid_usd', ARGV[9])
  redis.call('HINCRBY', KEYS[2], 'version', 1)
  redis.call('SADD', KEYS[3], ARGV[6])
end
redis.call('HSET', KEYS[1], 'status', 'done', 'content', ARGV[1], 'billed_cost_usd', ARGV[2],
  'provider_billed_cost_usd', ARGV[3], 'refunded_surplus_usd', ARGV[10])
redis.call('EXPIRE', KEYS[1], ARGV[4])
redis.call('ZREM', KEYS[4], ARGV[5])
return {'OK'}
"""

REFUND = """
if redis.call('HGET', KEYS[2], 'status') ~= 'running' then return {'STALE'} end
if redis.call('EXISTS', KEYS[1]) == 0 then return {'MISSING'} end
redis.call('HINCRBY', KEYS[1], 'free_usd', ARGV[5])
redis.call('HINCRBY', KEYS[1], 'bonus_usd', ARGV[6])
redis.call('HINCRBY', KEYS[1], 'paid_usd', ARGV[7])
redis.call('HINCRBY', KEYS[1], 'free_requests', ARGV[8])
redis.call('HINCRBY', KEYS[1], 'version', 1)
redis.call('HSET', KEYS[2], 'status', 'failed', 'error', ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[3])
redis.call('ZREM', KEYS[4], ARGV[4])
redis.call('SADD', KEYS[3], ARGV[1])
return {'OK'}
"""

TOP_UP = """
if redis.call('EXISTS', KEYS[2]) == 1 then return {'DUP'} end
if redis.call('EXISTS', KEYS[1]) == 0 then return {'MISSING'} end
redis.call('HINCRBY', KEYS[1], 'paid_usd', ARGV[3])
redis.call('HINCRBY', KEYS[1], 'bonus_usd', ARGV[4])
redis.call('HINCRBY', KEYS[1], 'free_requests', ARGV[5])
redis.call('HINCRBY', KEYS[1], 'version', 1)
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
redis.call('SADD', KEYS[3], ARGV[1])
return {'OK'}
"""

SEED_IF_ABSENT = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], 'free_usd', ARGV[1], 'bonus_usd', ARGV[2], 'paid_usd', ARGV[3],
  'free_requests', ARGV[4], 'version', ARGV[5])
return 1
"""

ADVANCE_VERSION = """
if redis.call('HGET', KEYS[1], 'version') ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'version', ARGV[2])
redis.call('SADD', KEYS[2], ARGV[3])
return 1
"""

RELEASE_LOAD_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""

CLEAR_DIRTY = """
if redis.call('EXISTS', KEYS[1]) == 0 or redis.call('HGET', KEYS[1], 'version') == ARGV[1] then
  redis.call('SREM', KEYS[2], ARGV[2])
  return 1
end
return 0
"""
