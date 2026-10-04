from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ("one" + "signal").lower()

ACTIVE_SURFACES = (
    ROOT / "backend" / "app",
    ROOT / "backend" / ".env.example",
    ROOT / "frontend" / "src",
    ROOT / "frontend" / "public",
    ROOT / "deployment" / "API_KEYS.md",
    ROOT / "deployment" / "nginx" / "albumfp-browser-headers.conf.example",
)

TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".html",
    ".css",
    ".js",
    ".json",
    ".md",
    ".txt",
    ".example",
}

def active_files():
    for target in ACTIVE_SURFACES:
        if target.is_file():
            yield target
            continue
        if not target.exists():
            continue
        for item in target.rglob("*"):
            if not item.is_file():
                continue
            if item.suffix.lower() in TEXT_SUFFIXES or item.name == ".env.example":
                yield item

class ExternalPushProviderRemovalTests(unittest.TestCase):

    def test_provider_is_absent_from_active_runtime_surface(self):
        hits = []
        for item in active_files():
            relative = item.relative_to(ROOT).as_posix()
            if PROVIDER in relative.lower():
                hits.append(f"{relative}:filename")
                continue
            try:
                text = item.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            if PROVIDER in text:
                hits.append(f"{relative}:content")
        self.assertEqual(
            [],
            sorted(hits),
            "retired external push provider remains:\n"
            + "\n".join(sorted(hits)),
        )

    def test_provider_frontend_runtime_is_removed(self):
        service = (
            ROOT / "frontend" / "src" / "app" / "core" / "services"
            / "push-notifications.ts"
        )
        worker = (
            ROOT / "frontend" / "public"
            / ("One" + "Signal" + "SDKWorker.js")
        )
        self.assertFalse(service.exists(), "external push Angular service still exists")
        self.assertFalse(worker.exists(), "external push SDK worker still exists")

    def test_frontend_has_no_push_provider_call_sites(self):
        relative_files = (
            "frontend/src/app/components/layout/dashboard-layout/dashboard-layout.ts",
            "frontend/src/app/core/services/auth.ts",
            "frontend/src/app/pages/dashboard/profile/profile.ts",
            "frontend/src/app/pages/dashboard/profile/profile.html",
            "frontend/src/index.html",
        )
        forbidden = (
            "PushNotifications",
            "push-notifications",
            "onesignal_app_id",
            "web_push_enabled",
            "notificationPreferences",
            "updateNotificationPreferences",
        )
        hits = []
        for relative in relative_files:
            text = (ROOT / relative).read_text(encoding="utf-8", errors="ignore")
            for token in forbidden:
                if token in text:
                    hits.append(f"{relative}:{token}")
        self.assertEqual(
            [],
            sorted(hits),
            "push-provider frontend surface remains:\n"
            + "\n".join(sorted(hits)),
        )

    def test_no_browser_push_or_notification_api_anywhere_in_the_frontend(self):
        """L12: las notificaciones son in-app. Ningún permiso del navegador,
        ninguna suscripción push, ningún worker que las reciba o las enseñe.
        L16 añadió un service worker normal de PWA (el de Angular, solo para
        la cáscara estática): eso está permitido; cualquier API de push no."""
        forbidden = (
            "Notification.requestPermission",
            "requestPermission(",
            "new Notification(",
            "pushManager",
            "PushSubscription",
            "SwPush",
            "requestSubscription",
            "showNotification",
            "notificationclick",
            "addEventListener('push'",
            'addEventListener("push"',
            "navigator.serviceWorker",
            "web_push_enabled",
            "/compartido-conmigo",
        )
        hits = []
        for item in (ROOT / "frontend" / "src").rglob("*"):
            if not item.is_file() or item.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = item.read_text(encoding="utf-8", errors="ignore")
            for token in forbidden:
                if token in text:
                    hits.append(f"{item.relative_to(ROOT).as_posix()}:{token}")
        self.assertEqual([], sorted(hits), "browser push surface:\n" + "\n".join(sorted(hits)))

    def test_the_only_service_worker_is_angulars_static_shell_worker(self):
        """L16. Se registra UN worker, el de @angular/service-worker, y solo con
        la configuración de la cáscara: sin `dataGroups` (ningún JSON de la
        API se guarda), y la navegación nunca responde con index.html en
        /api, /auth ni /_protected_media. No hay un worker propio en public/."""
        import json

        config = (ROOT / "frontend" / "src" / "app" / "app.config.ts").read_text(encoding="utf-8")
        self.assertIn("provideServiceWorker('ngsw-worker.js'", config)
        self.assertIn("enabled: !isDevMode()", config)
        ngsw = json.loads((ROOT / "frontend" / "ngsw-config.json").read_text(encoding="utf-8"))
        self.assertNotIn("dataGroups", ngsw)
        self.assertEqual("freshness", ngsw.get("navigationRequestStrategy"))
        for excluded in ("!/api/**", "!/auth/**", "!/_protected_media/**"):
            self.assertIn(excluded, ngsw["navigationUrls"])
        cached = [f for group in ngsw["assetGroups"] for f in group["resources"].get("files", [])]
        self.assertEqual([], [g for g in ngsw["assetGroups"] if g["resources"].get("urls")],
                         "ningún assetGroup puede apuntar a URLs de fuera")
        for pattern in cached:
            with self.subTest(pattern=pattern):
                self.assertFalse(pattern.startswith(("/api", "/auth", "/_protected_media", "/capturas", "/personas")))
        public_js = sorted(p.name for p in (ROOT / "frontend" / "public").rglob("*.js"))
        self.assertEqual(["theme-init.js"], public_js, "un worker propio en public/ saltaría esta revisión")

    def test_the_backend_push_compatibility_layer_is_gone(self):
        """El hook `send_push()` no-op, la bandera `web_push_enabled` y la ruta
        inexistente `/compartido-conmigo` eran la capa dormida que L12 sustituyó.
        La migración histórica 0010 sí conserva su texto: es historia inmutable."""
        forbidden = ("send_push", "web_push_enabled", "/compartido-conmigo")
        hits = []
        for item in (ROOT / "backend" / "app").rglob("*.py"):
            text = item.read_text(encoding="utf-8", errors="ignore")
            for token in forbidden:
                if token in text:
                    hits.append(f"{item.relative_to(ROOT).as_posix()}:{token}")
        self.assertEqual([], sorted(hits), "push compatibility layer:\n" + "\n".join(sorted(hits)))


if __name__ == "__main__":
    unittest.main()