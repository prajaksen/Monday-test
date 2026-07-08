import unittest

from app import extract_monday_context, render_tenant_user_sql


class MondayWebhookTests(unittest.TestCase):
    def test_extracts_tenant_and_user_details_from_monday_payload(self):
        payload = {
            "event": {"type": "status_changed"},
            "pulseId": 101,
            "pulseName": "Provision tenant",
            "boardId": 202,
            "boardName": "Acme Corp",
            "userId": 303,
            "userName": "Jane Doe",
            "userEmail": "jane@example.com",
        }

        context = extract_monday_context(payload)

        self.assertEqual(context["tenant_name"], "Acme Corp")
        self.assertEqual(context["user_email"], "jane@example.com")
        self.assertEqual(context["monday_item_id"], "101")
        self.assertEqual(context["monday_board_id"], "202")
        self.assertEqual(context["status"], "status_changed")

    def test_renders_sql_for_tenant_user_mapping(self):
        sql = render_tenant_user_sql(
            tenant_name="Acme Corp",
            user_email="jane@example.com",
            monday_item_id="101",
            monday_board_id="202",
            status="status_changed",
        )

        self.assertIn("CREATE TABLE IF NOT EXISTS tenant_user_mapping", sql)
        self.assertIn("INSERT INTO tenant_user_mapping", sql)
        self.assertIn("ON CONFLICT (tenant_name, user_email)", sql)


if __name__ == "__main__":
    unittest.main()
