import { Component, input } from '@angular/core';

export type IconName =
  | 'home' | 'albums' | 'heart' | 'share' | 'trash' | 'settings' | 'logout'
  | 'search' | 'upload' | 'plus' | 'user' | 'lock' | 'image' | 'video'
  | 'tag' | 'link' | 'close' | 'restore'
  | 'check' | 'moon' | 'sun' | 'menu' | 'sparkle' | 'layers' | 'eye' | 'eye-off' | 'pin' | 'edit' | 'cloud' | 'rain' | 'snow' | 'bell' | 'calendar' | 'download' | 'compass' | 'archive';

@Component({
  selector: 'app-icon',
  templateUrl: './icon.html',
  styleUrl: './icon.css',
})
export class Icon {
  name = input.required<IconName>();
  size = input(18);
  strokeWidth = input(1.8);
  /** Rellena la silueta con el color actual. Lo usa el corazón de favorito. */
  filled = input(false);
}
