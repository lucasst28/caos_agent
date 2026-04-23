#!/usr/bin/env python3
"""Test script to verify CAOS API endpoints and generate logs."""

import sys
import json
import requests
from time import sleep

API_URL = "http://localhost:8080"

def test_endpoint(name, url, expected_status=200):
    """Test an API endpoint."""
    try:
        print(f"\n🧪 Testing {name}...")
        response = requests.get(url, timeout=5)
        
        if response.status_code != expected_status:
            print(f"   ❌ Failed: HTTP {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
        
        data = response.json()
        print(f"   ✅ Success: HTTP {response.status_code}")
        print(f"   Response: {json.dumps(data, indent=2)[:300]}")
        return True
        
    except requests.exceptions.ConnectionError:
        print(f"   ❌ Connection failed - is server running?")
        return False
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def trigger_event():
    """Trigger a manual event to generate logs."""
    try:
        print(f"\n🚀 Triggering manual event...")
        
        payload = {
            "tenant_id": "test-tenant",
            "asset_id": "TEST-ASSET-001",
            "severity": "MEDIUM",
            "metric": "temperature",
            "value": 85.5,
            "payload": {
                "location": "Zona A",
                "description": "Teste de integração frontend"
            }
        }
        
        response = requests.post(
            f"{API_URL}/v1/events/trigger",
            json=payload,
            timeout=30
        )
        
        if response.status_code in (200, 201):
            data = response.json()
            print(f"   ✅ Event processed: {data.get('event_id')}")
            print(f"   Decision: {data.get('decision')}")
            print(f"   Processing time: {data.get('processing_time_ms')}ms")
            return True
        else:
            print(f"   ❌ Failed: HTTP {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("CAOS API Integration Tests")
    print("=" * 60)
    
    # Wait for server to be ready
    print("\n⏳ Waiting for server to be ready...")
    sleep(2)
    
    results = []
    
    # Test health
    results.append(test_endpoint("Health Check", f"{API_URL}/health"))
    
    # Test metrics
    results.append(test_endpoint("Metrics", f"{API_URL}/v1/metrics"))
    
    # Test logs (should be empty initially)
    results.append(test_endpoint("Logs", f"{API_URL}/v1/logs?limit=5"))
    
    # Trigger an event to generate logs
    results.append(trigger_event())
    
    # Check logs again (should have entries now)
    sleep(1)
    results.append(test_endpoint("Logs (after event)", f"{API_URL}/v1/logs?limit=10"))
    
    # Test dashboard
    print(f"\n🎨 Testing dashboard...")
    try:
        response = requests.get(f"{API_URL}/dashboard", timeout=5)
        if response.status_code == 200 and "CAOS" in response.text:
            print(f"   ✅ Dashboard is accessible")
            results.append(True)
        else:
            print(f"   ❌ Dashboard failed: HTTP {response.status_code}")
            results.append(False)
    except Exception as e:
        print(f"   ❌ Error: {e}")
        results.append(False)
    
    # Summary
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"✅ Passed: {passed}/{total}")
    
    if passed == total:
        print("\n🎉 All tests passed!")
        print(f"\n📊 Open dashboard at: {API_URL}/dashboard")
        print(f"📚 API docs at: {API_URL}/docs")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
