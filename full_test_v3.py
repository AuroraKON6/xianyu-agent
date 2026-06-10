"""Full end-to-end test for integrated multi-agent system v0.3.0.

Each agent now connects to real systems via workflow.
"""
import sys
import os
import requests

sys.path.insert(0, "E:/闲鱼/xianyu")
os.chdir("E:/闲鱼/xianyu")

BASE = "http://localhost:8788"

def test(name, method, path, data=None):
    url = f"{BASE}{path}"
    try:
        if method == "GET":
            r = requests.get(url, timeout=10)
        elif method == "POST":
            r = requests.post(url, json=data, timeout=120)
        elif method == "DELETE":
            r = requests.delete(url, timeout=10)
        else:
            print(f"  [FAIL] {name}: Unknown method")
            return None
        result = r.json()
        if isinstance(result, dict) and result.get("ok") is False:
            print(f"  [FAIL] {name}: {result}")
        else:
            print(f"  [OK] {name}")
        return result
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        return None


print("=" * 60)
print("Integrated Multi-Agent System Test v0.3.0")
print("=" * 60)

# 1. Health + Agents
print("\n[1] System Health")
test("Health", "GET", "/health")
result = test("List Agents", "GET", "/agents")
if result:
    for a in result:
        print(f"    {a['name']}: {a['backend']} ({a['status']})")

# 2. Customer Service Agent (DeepSeek V4 Flash -> AutoReplyEngine)
print("\n[2] Customer Service Agent (DeepSeek V4 Flash + AutoReplyEngine)")
test("Add CS Rule", "POST", "/agents/dispatch", {
    "agent": "customer_service", "action": "add_rule",
    "params": {"keyword": "warranty", "reply_text": "1 year warranty included"}
})
result = test("CS Reply", "POST", "/agents/customer-service/reply", {
    "message": "Is this still available?",
    "product_title": "iPhone 15 Pro Max",
    "product_price": "8999",
    "cookie_id": "test_001",
})
if result and result.get("reply"):
    print(f"    Reply: {result['reply'][:60]}")
test("Get CS Rules", "POST", "/agents/dispatch", {
    "agent": "customer_service", "action": "get_rules", "params": {}
})

# 3. Listing Agent (workflow.create_listing)
print("\n[3] Listing Agent (workflow.create_listing)")
result = test("Create Listing", "POST", "/agents/listing/create", {
    "text": "Brand new Samsung Galaxy S24 Ultra, 512GB, Titanium Black, price 9499 yuan",
    "publish": False,
})
if result and result.get("draft"):
    print(f"    Title: {result['draft'].get('title', 'N/A')}")
    print(f"    Price: {result['draft'].get('price', 'N/A')}")

# 4. Discovery Agent (ProductDiscovery)
print("\n[4] Discovery Agent (ProductDiscovery)")
test("List Discovery Tasks", "POST", "/agents/dispatch", {
    "agent": "discovery", "action": "list_tasks", "params": {}
})
test("Analyze Market", "POST", "/agents/discovery/analyze", {
    "category": "smartphone"
})

# 5. Order Agent (DB queries)
print("\n[5] Order Agent (Database)")
result = test("List Orders", "POST", "/agents/dispatch", {
    "agent": "order", "action": "list_orders", "params": {}
})
if result:
    print(f"    Orders: {result.get('result', {}).get('count', 0)}")

# 6. GitHub Agent (GitHub API)
print("\n[6] GitHub Agent (GitHub API)")
test("Analyze Repo", "POST", "/agents/github/analyze", {
    "repo": "anthropics/claude-code",
    "description": "CLI tool for Claude AI",
    "stars": 5000,
})

# 7. Analytics Agent (DB queries)
print("\n[7] Analytics Agent (Database)")
result = test("Dashboard", "POST", "/agents/dispatch", {
    "agent": "analytics", "action": "get_dashboard_data", "params": {}
})
if result and result.get("result"):
    d = result["result"]
    print(f"    Accounts: {d.get('accounts', 0)}, Orders: {d.get('total_orders', 0)}")

result = test("Calculate Profit", "POST", "/agents/analytics/profit", {
    "cost": 500, "price": 800, "platform_fee_rate": 0.08, "shipping_cost": 10,
})
if result:
    print(f"    Profit: {result.get('profit', 'N/A')} ({result.get('profit_rate', 0):.0%})")

# 8. Hermes Agent (Orchestration)
print("\n[8] Hermes Agent (Orchestration)")
result = test("Hermes - Status", "POST", "/agents/hermes/message", {
    "message": "status"
})
if result and result.get("reply"):
    print(f"    {result['reply'][:80]}")

result = test("Hermes - Analytics", "POST", "/agents/hermes/message", {
    "message": "分析一下店铺数据"
})
if result and result.get("reply"):
    print(f"    {result['reply'][:80]}")

# 9. Agent Dispatch
print("\n[9] Agent Dispatch")
test("Dispatch Task", "POST", "/agents/dispatch", {
    "agent": "order", "action": "list_orders", "params": {}
})
test("Task History", "GET", "/agents/tasks?limit=5")

# 10. Legacy endpoints still work
print("\n[10] Legacy Compatibility")
test("Legacy Health", "GET", "/health")
test("Accounts", "GET", "/accounts")
test("Ruflo Status", "GET", "/ruflo/status")

print("\n" + "=" * 60)
print("Integrated Multi-Agent Test Complete")
print("=" * 60)
