import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ActivityEntry, NotificationsApi } from '../../../core/services/notifications';
import { activityDetail, activityText } from '../../../core/utils/activity-copy';
import { pagedList } from '../../../core/utils/paged-list';
import { Icon, IconName } from '../icon/icon';

const ICONS: Record<ActivityEntry['event_type'], IconName> = {
  album_invite_created: 'share',
  share_claimed: 'check',
  asset_uploaded: 'plus',
  asset_added: 'layers',
  asset_removed: 'close',
  cover_changed: 'image',
  share_permission_changed: 'settings',
};

/**
 * El historial de colaboración de un álbum normal (L12), más nuevo primero.
 * Solo se monta para quien el servidor dice que puede verlo; si aun así
 * responde 403 (un acceso revocado mientras tanto), se enseña el error y ya.
 */
@Component({
  selector: 'app-album-activity',
  imports: [DatePipe, Icon, RouterLink],
  templateUrl: './album-activity.html',
  styleUrl: './album-activity.css',
})
export class AlbumActivity implements OnInit {
  private readonly api = inject(NotificationsApi);

  albumId = input.required<number>();

  readonly list = pagedList<ActivityEntry>(page => this.api.albumActivity(this.albumId(), page));
  readonly text = activityText;
  readonly detail = activityDetail;
  readonly icon = (entry: ActivityEntry) => ICONS[entry.event_type];

  ngOnInit() { this.list.reset(); }

  isAssetEvent(entry: ActivityEntry) {
    return entry.event_type === 'asset_uploaded' || entry.event_type === 'asset_added' || entry.event_type === 'asset_removed';
  }
}
