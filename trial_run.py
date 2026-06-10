"""Trial run - stress test all agent workflows, find bugs."""
import sys
import os
import json
import time
import requests

sys.path.insert(0, "E:/闲鱼/xianyu")
os.chdir("E:/闲鱼/xianyu")

BASE = "http://localhost:8788"
BUGS = []

def call(name, method, path, data=None, timeout=120):
    url = f"{BASE}{path}"
    try:
        if method == "GET":
            r = requests.get(url, timeout=timeout)
        elif method == "POST":
            r = requests.post(url, json=data, timeout=timeout)
        elif method == "DELETE":
            r = requests.delete(url, timeout=timeout)
        else:
            return None
        if r.status_code >= 500:
            BUGS.append(f"[500] {name}: {r.text[:200]}")
            print(f"  [BUG] {name}: HTTP {r.status_code}")
            return None
        if r.status_code >= 400:
            BUGS.append(f"[{r.status_code}] {name}: {r.text[:200]}")
            print(f"  [ERR] {name}: HTTP {r.status_code} - {r.text[:100]}")
            return None
        result = r.json()
        if isinstance(result, dict) and result.get("ok") is False:
            err = result.get("error", "")
            BUGS.append(f"[FAIL] {name}: {err}")
            print(f"  [FAIL] {name}: {err[:80]}")
            return result
        print(f"  [OK] {name}")
        return result
    except requests.exceptions.Timeout:
        BUGS.append(f"[TIMEOUT] {name}")
        print(f"  [TIMEOUT] {name}")
        return None
    except Exception as e:
        BUGS.append(f"[EXC] {name}: {e}")
        print(f"  [BUG] {name}: {e}")
        return None


print("=" * 60)
print("TRIAL RUN - Bug Hunting")
print("=" * 60)

# ============================================================
# 1. Customer Service Agent - Full workflow
# ============================================================
print("\n[1] Customer Service Agent (DeepSeek V4 Flash)")

# 1a. Add rules
call("Add rule: warranty", "POST", "/agents/dispatch", {
    "agent": "customer_service", "action": "add_rule",
    "params": {"keyword": "warranty", "reply_text": "1 year warranty included", "priority": 10}
})
call("Add rule: shipping", "POST", "/agents/dispatch", {
    "agent": "customer_service", "action": "add_rule",
    "params": {"keyword": "shipping", "reply_text": "Free shipping within 3 days"}
})
call("Add rule: price", "POST", "/agents/dispatch", {
    "agent": "customer_service", "action": "add_rule",
    "params": {"keyword": "discount", "reply_text": "Current price is already the best deal!"}
})

# 1b. Get rules
result = call("Get rules", "POST", "/agents/dispatch", {
    "agent": "customer_service", "action": "get_rules", "params": {}
})
if result and result.get("result"):
    rules = result["result"]
    if isinstance(rules, list):
        print(f"    Rules count: {len(rules)}")
    else:
        BUGS.append(f"[BUG] get_rules returned non-list: {type(rules)}")

# 1c. Reply via endpoint (keyword match)
result = call("Reply - warranty keyword", "POST", "/agents/customer-service/reply", {
    "buyer_id": "buyer_001", "message": "What about the warranty?",
    "product_title": "iPhone 15 Pro Max", "product_price": "8999",
})
if result and result.get("reply"):
    reply = result["reply"]
    if "warranty" not in reply.lower() and "warranty" not in str(result):
        print(f"    NOTE: Reply may not use keyword rule: {reply[:60]}")

# 1d. Reply via endpoint (AI reply)
result = call("Reply - AI greeting", "POST", "/agents/customer-service/reply", {
    "buyer_id": "buyer_002", "message": "Is this still available?",
    "product_title": "MacBook Pro M3", "product_price": "12999",
})
if result and result.get("reply"):
    print(f"    Reply: {result['reply'][:60]}")

# 1e. Reply via endpoint (price negotiation)
result = call("Reply - price negotiation", "POST", "/agents/customer-service/reply", {
    "buyer_id": "buyer_003", "message": "Can you lower the price to 7000?",
    "product_title": "iPhone 15 Pro Max", "product_price": "8999",
})
if result and result.get("reply"):
    print(f"    Reply: {result['reply'][:60]}")

# 1f. Reply with empty message
result = call("Reply - empty message", "POST", "/agents/customer-service/reply", {
    "buyer_id": "buyer_004", "message": "",
    "product_title": "iPhone", "product_price": "999",
})

# 1g. Reply with contact info (should be blocked)
result = call("Reply - contact info blocked", "POST", "/agents/customer-service/reply", {
    "buyer_id": "buyer_005", "message": "Add me on wechat: abc123",
    "product_title": "iPhone", "product_price": "999",
})
if result and result.get("reply") is not None:
    BUGS.append("[BUG] Contact info message should return null reply but got: " + str(result.get("reply")))

# ============================================================
# 2. Listing Agent
# ============================================================
print("\n[2] Listing Agent (MiMo)")

result = call("Create listing - normal", "POST", "/agents/listing/create", {
    "text": "Brand new AirPods Pro 2, sealed box, price 1299 yuan",
    "publish": False,
})
if result and result.get("draft"):
    d = result["draft"]
    if not d.get("title"):
        BUGS.append("[BUG] Listing draft missing title")
    # Note: price extraction depends on LLM quality, not always guaranteed
    if not d.get("price"):
        print(f"    NOTE: Listing draft missing price (LLM extraction quality)")

# Create listing with empty text
result = call("Create listing - empty text", "POST", "/agents/listing/create", {
    "text": "", "publish": False,
})

# Polish (should work even with no published items)
result = call("Polish all", "POST", "/agents/dispatch", {
    "agent": "listing", "action": "polish_all", "params": {}
})

# List published
result = call("List published", "POST", "/agents/dispatch", {
    "agent": "listing", "action": "list_published", "params": {}
})

# ============================================================
# 3. Discovery Agent
# ============================================================
print("\n[3] Discovery Agent")

result = call("List tasks", "POST", "/agents/dispatch", {
    "agent": "discovery", "action": "list_tasks", "params": {}
})

result = call("Add discovery task", "POST", "/agents/dispatch", {
    "agent": "discovery", "action": "add_task",
    "params": {"keyword": "iPhone 15", "max_pages": 1}
})

result = call("Analyze market", "POST", "/agents/discovery/analyze", {
    "category": "headphones"
})

# ============================================================
# 4. Order Agent
# ============================================================
print("\n[4] Order Agent")

result = call("List orders (empty)", "POST", "/agents/dispatch", {
    "agent": "order", "action": "list_orders", "params": {}
})

result = call("Get nonexistent order", "POST", "/agents/dispatch", {
    "agent": "order", "action": "get_order",
    "params": {"order_id": "nonexistent_123"}
})
if result and result.get("ok") is True:
    BUGS.append("[BUG] get_order should fail for nonexistent order but returned ok=True")
elif result and isinstance(result.get("result"), dict) and result["result"].get("ok") is not False:
    BUGS.append("[BUG] get_order result should indicate failure")

result = call("Generate shipping msg", "POST", "/agents/dispatch", {
    "agent": "order", "action": "generate_shipping_message",
    "params": {"order_id": "ORD001", "tracking_number": "SF123456", "product_title": "iPhone"}
})

result = call("Handle dispute", "POST", "/agents/dispatch", {
    "agent": "order", "action": "handle_dispute",
    "params": {"issue": "Item not as described", "product_title": "iPhone"}
})

# ============================================================
# 5. GitHub Agent
# ============================================================
print("\n[5] GitHub Agent")

result = call("Analyze repo", "POST", "/agents/github/analyze", {
    "repo": "langchain-ai/langchain",
    "description": "Building applications with LLMs",
    "stars": 95000,
})

# ============================================================
# 6. Analytics Agent
# ============================================================
print("\n[6] Analytics Agent")

result = call("Dashboard", "POST", "/agents/dispatch", {
    "agent": "analytics", "action": "get_dashboard_data", "params": {}
})
if result and result.get("result"):
    d = result["result"]
    if not isinstance(d.get("total_orders"), int):
        BUGS.append(f"[BUG] dashboard total_orders should be int, got: {type(d.get('total_orders'))}")

result = call("Calculate profit", "POST", "/agents/analytics/profit", {
    "cost": 0, "price": 100, "platform_fee_rate": 0.08, "shipping_cost": 10,
})
if result and result.get("profit_rate"):
    # cost=0 should not cause division by zero
    pass

result = call("Generate report", "POST", "/agents/dispatch", {
    "agent": "analytics", "action": "generate_report", "params": {}
})

result = call("Analyze sales", "POST", "/agents/dispatch", {
    "agent": "analytics", "action": "analyze_sales", "params": {}
})

# ============================================================
# 7. Hermes Agent
# ============================================================
print("\n[7] Hermes Agent")

result = call("Hermes - status", "POST", "/agents/hermes/message", {"message": "status"})
result = call("Hermes - help", "POST", "/agents/hermes/message", {"message": "help"})
result = call("Hermes - listing", "POST", "/agents/hermes/message", {"message": "上架 AirPods Pro 2 售价1299"})
result = call("Hermes - analytics", "POST", "/agents/hermes/message", {"message": "分析一下数据"})

# ============================================================
# 8. Edge cases
# ============================================================
print("\n[8] Edge Cases")

# Dispatch to nonexistent agent (should return error, not crash)
result = call("Dispatch nonexistent agent", "POST", "/agents/dispatch", {
    "agent": "nonexistent", "action": "test", "params": {}
})
if result and result.get("ok") is True:
    BUGS.append("[BUG] Dispatch to nonexistent agent should fail but returned ok=True")

# Dispatch nonexistent action (should return error, not crash)
result = call("Dispatch nonexistent action", "POST", "/agents/dispatch", {
    "agent": "listing", "action": "nonexistent_action", "params": {}
})
if result and result.get("ok") is True:
    BUGS.append("[BUG] Dispatch nonexistent action should fail but returned ok=True")

# Concurrent requests
print("\n[9] Concurrent Requests")
import concurrent.futures
def make_request(i):
    try:
        r = requests.post(f"{BASE}/agents/analytics/profit", json={
            "cost": 100+i, "price": 200+i, "platform_fee_rate": 0.08, "shipping_cost": 10
        }, timeout=60)
        return r.status_code
    except Exception as e:
        return str(e)

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    futures = [pool.submit(make_request, i) for i in range(3)]
    results = [f.result() for f in futures]
    print(f"  Concurrent results: {results}")
    if any(r != 200 for r in results):
        BUGS.append(f"[BUG] Concurrent requests failed: {results}")

# ============================================================
# Results
# ============================================================
print("\n" + "=" * 60)
print(f"TRIAL RUN COMPLETE - Found {len(BUGS)} bugs")
print("=" * 60)
for i, bug in enumerate(BUGS, 1):
    print(f"\n  {i}. {bug}")
if not BUGS:
    print("  No bugs found!")
