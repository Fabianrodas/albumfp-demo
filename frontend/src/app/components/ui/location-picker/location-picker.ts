import { Component, input, output, signal } from '@angular/core';
import { Icon } from '../icon/icon';

@Component({
  selector: 'app-location-picker',
  imports: [Icon],
  templateUrl: './location-picker.html',
  styleUrl: './location-picker.css',
})
export class LocationPicker {
  readonly initial = input<{ lat: number; lon: number } | null>(null);
  readonly latitude = signal('');
  readonly longitude = signal('');
  readonly marker = signal<{ lat: number; lon: number } | null>(null);
  readonly error = signal('');

  readonly picked = output<{ latitude: number; longitude: number }>();

  constructor() {
    const start = this.initial();
    if (start) {
      this.latitude.set(String(start.lat));
      this.longitude.set(String(start.lon));
      this.marker.set({ lat: start.lat, lon: start.lon });
    }
  }

  onLatitudeInput(value: string) {
    this.latitude.set(value.trim());
    this.updatePoint();
  }

  onLongitudeInput(value: string) {
    this.longitude.set(value.trim());
    this.updatePoint();
  }

  private updatePoint() {
    const latitude = Number(this.latitude());
    const longitude = Number(this.longitude());
    if (!this.latitude() && !this.longitude()) {
      this.error.set('');
      this.marker.set(null);
      return;
    }
    if (!this.latitude() || !this.longitude() || !Number.isFinite(latitude) || !Number.isFinite(longitude)) {
      this.error.set('Completa ambas coordenadas en grados decimales.');
      this.marker.set(null);
      return;
    }
    if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
      this.error.set('La latitud va de −90 a 90 y la longitud de −180 a 180.');
      this.marker.set(null);
      return;
    }
    this.error.set('');
    this.marker.set({ lat: latitude, lon: longitude });
    this.picked.emit({ latitude, longitude });
  }
}
