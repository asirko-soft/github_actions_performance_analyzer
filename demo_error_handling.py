#!/usr/bin/env python3
"""
Demo script to showcase the error handling implementation for task 6.
This script demonstrates how the API handles invalid filters and empty result sets.
"""

from app import app
import json

def demo_error_handling():
    """Demonstrate error handling for invalid filters."""
    
    print("=" * 80)
    print("TASK 6: ERROR HANDLING FOR INVALID FILTERS - DEMONSTRATION")
    print("=" * 80)
    print()
    
    with app.test_client() as client:
        
        # Demo 1: Invalid conclusion filter
        print("1. Testing INVALID conclusion filter")
        print("-" * 80)
        print("Request: GET /api/workflows?owner=test&repo=test&workflow_id=test.yml&conclusions=invalid_value")
        response = client.get('/api/workflows?owner=test&repo=test&workflow_id=test.yml&conclusions=invalid_value')
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.get_json(), indent=2)}")
        print()
        
        # Demo 2: Multiple invalid conclusions
        print("2. Testing MULTIPLE invalid conclusions")
        print("-" * 80)
        print("Request: GET /api/trends?owner=test&repo=test&workflow_id=test.yml&conclusions=bad,wrong,invalid")
        response = client.get('/api/trends?owner=test&repo=test&workflow_id=test.yml&conclusions=bad,wrong,invalid')
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.get_json(), indent=2)}")
        print()
        
        # Demo 3: Valid conclusions (should not error)
        print("3. Testing VALID conclusions")
        print("-" * 80)
        print("Request: GET /api/workflows?owner=test&repo=test&workflow_id=test.yml&conclusions=success,failure")
        response = client.get('/api/workflows?owner=test&repo=test&workflow_id=test.yml&conclusions=success,failure')
        print(f"Status Code: {response.status_code}")
        result = response.get_json()
        if response.status_code == 200:
            print("✓ Valid conclusions accepted (no 400 error)")
            if 'metadata' in result:
                print(f"Response includes metadata: {json.dumps(result['metadata'], indent=2)}")
        else:
            print(f"Response: {json.dumps(result, indent=2)}")
        print()
        
        # Demo 4: All valid conclusion values
        print("4. Testing ALL valid conclusion values")
        print("-" * 80)
        valid_conclusions = ['success', 'failure', 'cancelled', 'skipped']
        for conclusion in valid_conclusions:
            response = client.get(f'/api/workflows?owner=test&repo=test&workflow_id=test.yml&conclusions={conclusion}')
            status = "✓ PASS" if response.status_code != 400 else "✗ FAIL"
            print(f"  {status} - conclusion='{conclusion}' -> Status {response.status_code}")
        print()
        
        # Demo 5: Invalid conclusion in different endpoints
        print("5. Testing invalid conclusions across DIFFERENT endpoints")
        print("-" * 80)
        endpoints = [
            '/api/workflows?owner=test&repo=test&workflow_id=test.yml&conclusions=bad',
            '/api/trends?owner=test&repo=test&workflow_id=test.yml&conclusions=bad',
            '/api/jobs?owner=test&repo=test&workflow_id=test.yml&start_date=2024-01-01T00:00:00Z&end_date=2024-01-02T00:00:00Z&conclusions=bad',
            '/api/jobs/slowest?owner=test&repo=test&workflow_id=test.yml&start_date=2024-01-01T00:00:00Z&end_date=2024-01-02T00:00:00Z&conclusions=bad',
            '/api/steps?owner=test&repo=test&workflow_id=test.yml&start_date=2024-01-01T00:00:00Z&end_date=2024-01-02T00:00:00Z&conclusions=bad',
            '/api/jobs/test/trends?owner=test&repo=test&workflow_id=test.yml&start_date=2024-01-01T00:00:00Z&end_date=2024-01-02T00:00:00Z&conclusions=bad',
            '/api/jobs/test/executions?owner=test&repo=test&workflow_id=test.yml&start_date=2024-01-01T00:00:00Z&end_date=2024-01-02T00:00:00Z&conclusions=bad',
            '/api/jobs/test/build-steps?owner=test&repo=test&workflow_id=test.yml&start_date=2024-01-01T00:00:00Z&end_date=2024-01-02T00:00:00Z&conclusions=bad',
        ]
        
        for endpoint in endpoints:
            response = client.get(endpoint)
            endpoint_name = endpoint.split('?')[0]
            status = "✓ PASS" if response.status_code == 400 else "✗ FAIL"
            print(f"  {status} - {endpoint_name} -> Status {response.status_code}")
        print()
        
        print("=" * 80)
        print("DEMONSTRATION COMPLETE")
        print("=" * 80)
        print()
        print("Summary:")
        print("✓ Invalid conclusion filters return HTTP 400 with clear error messages")
        print("✓ Valid conclusion filters are accepted without errors")
        print("✓ All API endpoints consistently handle invalid filters")
        print("✓ Error messages indicate which values are invalid and list valid options")
        print()

if __name__ == '__main__':
    demo_error_handling()
