import unittest

from app.api.shares import shape_shared_media


class SharedMediaShapeTests(unittest.TestCase):
    def setUp(self):
        self.media = {
            'id': 12,
            'user_id': 44,
            'album_id': 8,
            'file_type': 'image',
            'title': 'Atardecer',
            'caption': 'Desde el malecón',
            'is_favorite': True,
            'taken_at': '2026-08-30T18:00:00',
            'created_at': '2026-08-30T19:00:00',
            'created_by': 44,
            'created_by_username': 'propietaria',
        }

    def test_hidden_metadata_keeps_only_the_data_needed_to_render_the_media(self):
        self.assertEqual(
            {
                'id': 12,
                'album_id': 8,
                'file_type': 'image',
                'title': 'Atardecer',
                'caption': 'Desde el malecón',
            },
            shape_shared_media(self.media, show_metadata=False),
        )

    def test_visible_metadata_keeps_the_complete_media_shape(self):
        self.assertEqual(self.media, shape_shared_media(self.media, show_metadata=True))

    def test_hidden_metadata_applies_the_same_boundary_to_every_list_item(self):
        second = {**self.media, 'id': 13, 'title': 'Noche'}
        shaped = [shape_shared_media(media, show_metadata=False) for media in (self.media, second)]
        self.assertEqual(
            [
                {'id': 12, 'album_id': 8, 'file_type': 'image', 'title': 'Atardecer', 'caption': 'Desde el malecón'},
                {'id': 13, 'album_id': 8, 'file_type': 'image', 'title': 'Noche', 'caption': 'Desde el malecón'},
            ],
            shaped,
        )
