"""Lua sources. Scripts compare only ``version``; money arrives pre-computed as strings.

RESERVE  KEYS bal, gen, dirty, inflight
         ARGV expected_version, free, bonus, paid, free_requests, user_id, dialog_id, model_name,
              plan_free, plan_bonus, plan_paid, plan_free_requests, authorized_cost, started_at,
              ttl, client_request_id
SETTLE   KEYS gen, bal, dirty, inflight
         ARGV content, billed (charged), provider_billed, ttl, client_request_id, user_id,
              expected_version ('' = no balance change), free, bonus, paid, free_requests,
              refunded_surplus
REFUND   KEYS bal, gen, dirty, inflight
         ARGV expected_version, free, bonus, paid, free_requests, user_id, error, ttl,
              client_request_id
"""

BALANCE_FIELDS = ("free_usd", "bonus_usd", "paid_usd", "free_requests")

RESERVE = """
if redis.call('EXISTS', KEYS[2]) == 1 then
  local r = redis.call('HMGET', KEYS[2], 'status', 'user_id', 'dialog_id', 'model_name',
    'plan_free_usd', 'plan_bonus_usd', 'plan_paid_usd', 'plan_free_requests',
    'authorized_cost_usd', 'started_at', 'content', 'billed_cost_usd',
    'provider_billed_cost_usd', 'refunded_surplus_usd', 'error')
  for i = 1, 15 do if not r[i] then r[i] = '' end end
  return {'DUP', unpack(r)}
end
if redis.call('EXISTS', KEYS[1]) == 0 then return {'MISSING'} end
local c = redis.call('HMGET', KEYS[1], 'version', 'free_usd', 'bonus_usd', 'paid_usd',
  'free_requests')
if c[1] ~= ARGV[1] then return {'CONFLICT', c[2], c[3], c[4], c[5], c[1]} end
local v = redis.call('HINCRBY', KEYS[1], 'version', 1)
redis.call('HSET', KEYS[1], 'free_usd', ARGV[2], 'bonus_usd', ARGV[3], 'paid_usd', ARGV[4],
  'free_requests', ARGV[5])
redis.call('HSET', KEYS[2], 'status', 'running', 'user_id', ARGV[6], 'dialog_id', ARGV[7],
  'model_name', ARGV[8], 'plan_free_usd', ARGV[9], 'plan_bonus_usd', ARGV[10],
  'plan_paid_usd', ARGV[11], 'plan_free_requests', ARGV[12], 'authorized_cost_usd', ARGV[13],
  'started_at', ARGV[14])
redis.call('EXPIRE', KEYS[2], ARGV[15])
redis.call('ZADD', KEYS[4], ARGV[14], ARGV[16])
redis.call('SADD', KEYS[3], ARGV[6])
return {'OK', tostring(v)}
"""

SETTLE = """
if redis.call('HGET', KEYS[1], 'status') ~= 'running' then return {'STALE'} end
if ARGV[7] ~= '' then
  if redis.call('EXISTS', KEYS[2]) == 0 then return {'MISSING'} end
  local c = redis.call('HMGET', KEYS[2], 'version', 'free_usd', 'bonus_usd', 'paid_usd',
    'free_requests')
  if c[1] ~= ARGV[7] then return {'CONFLICT', c[2], c[3], c[4], c[5], c[1]} end
  redis.call('HINCRBY', KEYS[2], 'version', 1)
  redis.call('HSET', KEYS[2], 'free_usd', ARGV[8], 'bonus_usd', ARGV[9], 'paid_usd', ARGV[10],
    'free_requests', ARGV[11])
  redis.call('SADD', KEYS[3], ARGV[6])
end
redis.call('HSET', KEYS[1], 'status', 'done', 'content', ARGV[1], 'billed_cost_usd', ARGV[2],
  'provider_billed_cost_usd', ARGV[3], 'refunded_surplus_usd', ARGV[12])
redis.call('EXPIRE', KEYS[1], ARGV[4])
redis.call('ZREM', KEYS[4], ARGV[5])
return {'OK'}
"""

REFUND = """
if redis.call('HGET', KEYS[2], 'status') ~= 'running' then return {'STALE'} end
if redis.call('EXISTS', KEYS[1]) == 0 then return {'MISSING'} end
local c = redis.call('HMGET', KEYS[1], 'version', 'free_usd', 'bonus_usd', 'paid_usd',
  'free_requests')
if c[1] ~= ARGV[1] then return {'CONFLICT', c[2], c[3], c[4], c[5], c[1]} end
redis.call('HINCRBY', KEYS[1], 'version', 1)
redis.call('HSET', KEYS[1], 'free_usd', ARGV[2], 'bonus_usd', ARGV[3], 'paid_usd', ARGV[4],
  'free_requests', ARGV[5])
redis.call('HSET', KEYS[2], 'status', 'failed', 'error', ARGV[7])
redis.call('EXPIRE', KEYS[2], ARGV[8])
redis.call('ZREM', KEYS[4], ARGV[9])
redis.call('SADD', KEYS[3], ARGV[6])
return {'OK'}
"""

TOP_UP = """
if redis.call('EXISTS', KEYS[2]) == 1 then return {'DUP'} end
if redis.call('EXISTS', KEYS[1]) == 0 then return {'MISSING'} end
local c = redis.call('HMGET', KEYS[1], 'version', 'free_usd', 'bonus_usd', 'paid_usd',
  'free_requests')
if c[1] ~= ARGV[1] then return {'CONFLICT', c[2], c[3], c[4], c[5], c[1]} end
redis.call('HINCRBY', KEYS[1], 'version', 1)
redis.call('HSET', KEYS[1], 'free_usd', ARGV[2], 'bonus_usd', ARGV[3], 'paid_usd', ARGV[4],
  'free_requests', ARGV[5])
redis.call('SET', KEYS[2], ARGV[6], 'EX', ARGV[7])
redis.call('SADD', KEYS[3], ARGV[6])
return {'OK'}
"""

SEED_IF_ABSENT = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], 'free_usd', ARGV[1], 'bonus_usd', ARGV[2], 'paid_usd', ARGV[3],
  'free_requests', ARGV[4], 'version', ARGV[5])
return 1
"""

CLEAR_DIRTY = """
if redis.call('EXISTS', KEYS[1]) == 0 or redis.call('HGET', KEYS[1], 'version') == ARGV[1] then
  redis.call('SREM', KEYS[2], ARGV[2])
  return 1
end
return 0
"""
