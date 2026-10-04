import unittest

from app import db


class DatabaseSafetyTests(unittest.TestCase):
    def test_accepts_the_allowlisted_development_database_on_localhost(self):
        parser = getattr(db, "parse_demo_database_url", None)
        self.assertTrue(callable(parser), "the guarded URL parser must be available")

        url = "postgresql+psycopg2://demo:secret@localhost:55432/albumfp_demo"
        self.assertEqual(parser(url, purpose="development"), url)

    def test_accepts_the_ipv4_loopback_address(self):
        url = "postgresql+psycopg2://demo:secret@127.0.0.1:55432/albumfp_demo"
        self.assertEqual(db.parse_demo_database_url(url), url)

    def test_accepts_the_ipv6_loopback_address(self):
        url = "postgresql+psycopg2://demo:secret@[::1]:55432/albumfp_demo"
        self.assertEqual(db.parse_demo_database_url(url), url)

    def test_accepts_only_the_dedicated_test_database_for_test_purpose(self):
        url = "postgresql+psycopg2://demo:secret@127.0.0.1:55432/albumfp_demo_test"
        self.assertEqual(db.parse_demo_database_url(url, purpose="test"), url)

    def test_test_purpose_refuses_the_development_database(self):
        url = "postgresql+psycopg2://demo:secret@localhost:55432/albumfp_demo"
        with self.assertRaises(ValueError):
            db.parse_demo_database_url(url, purpose="test")

    def test_refuses_the_real_product_database_name(self):
        url = "postgresql+psycopg2://demo:secret@localhost:55432/albumfp"
        with self.assertRaises(ValueError):
            db.parse_demo_database_url(url)

    def test_refuses_an_unlisted_database_name(self):
        url = "postgresql+psycopg2://demo:secret@localhost:55432/customer_data"
        with self.assertRaises(ValueError):
            db.parse_demo_database_url(url)

    def test_refuses_non_loopback_hosts(self):
        for host in ("albumfp.com", "db.example.test", "127.0.0.2", "db.localhost"):
            with self.subTest(host=host):
                url = f"postgresql+psycopg2://demo:secret@{host}:55432/albumfp_demo"
                with self.assertRaises(ValueError):
                    db.parse_demo_database_url(url)

    def test_refuses_query_parameters_that_could_override_connection_settings(self):
        url = "postgresql+psycopg2://demo:secret@localhost:55432/albumfp_demo?host=db.example.test"
        with self.assertRaises(ValueError):
            db.parse_demo_database_url(url)

    def test_refuses_fragments_and_malformed_ports(self):
        urls = (
            "postgresql+psycopg2://demo:secret@localhost:55432/albumfp_demo#other",
            "postgresql+psycopg2://demo:secret@localhost:99999/albumfp_demo",
        )
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    db.parse_demo_database_url(url)

    def test_refuses_non_postgresql_schemes_and_unknown_purposes(self):
        with self.assertRaises(ValueError):
            db.parse_demo_database_url("sqlite:///albumfp_demo")
        with self.assertRaises(ValueError):
            db.parse_demo_database_url(
                "postgresql://localhost/albumfp_demo", purpose="bootstrap"
            )

    def test_test_mode_never_falls_back_to_the_development_url(self):
        loader = getattr(db, "database_url_from_environment", None)
        self.assertTrue(callable(loader), "database URL selection must be available")
        environ = {
            "DATABASE_URL": "postgresql://demo:secret@localhost:55432/albumfp_demo"
        }
        with self.assertRaises(RuntimeError):
            loader(environ, purpose="test")


if __name__ == "__main__":
    unittest.main()
