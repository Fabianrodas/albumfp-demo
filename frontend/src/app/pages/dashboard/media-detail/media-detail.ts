import { Component, ElementRef, HostListener, OnDestroy, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { forkJoin, of, Subscription } from 'rxjs';
import { LocationPicker } from '../../../components/ui/location-picker/location-picker';
import { Modal } from '../../../components/ui/modal/modal';
import { MediaComments } from '../../../components/ui/media-comments/media-comments';
import { Icon } from '../../../components/ui/icon/icon';
import { AlbumCapability, AlbumRole, canDeleteMedia, canEditAlbum, canEditMedia, canManageAlbum, canOrganizeMedia } from '../../../core/models/album-permissions';
import { Album, AlbumApi, ContextResults, Media, MediaAlbumRef, MediaContext, MediaExif, MediaMetadata, MediaOcr, MediaUpdatePayload, Tag, TagSuggestion } from '../../../core/services/album-api';
import { Confirm } from '../../../core/services/confirm';
import { Theme } from '../../../core/services/theme';
import { Toast } from '../../../core/services/toast';
import { upsertTagSorted } from '../../../core/utils/tag-catalog';
import { isCurrentMediaRequest, MediaDirection, mediaDetailLink, navigationKey, neighboringMediaId } from '../../../core/utils/media-navigation';
import { pagedList } from '../../../core/utils/paged-list';
import { advancedSearchFromParams, advancedSearchToParams } from '../../../core/utils/advanced-search';
import { captureFrame } from '../../../core/utils/video-frame';

/** Ventana de ±45 min alrededor del amanecer/atardecer para la etiqueta
 * "Cerca del…". Es una regla local y explícita, no un dato del proveedor. */
const SOLAR_WINDOW_MS = 45 * 60 * 1000;

/** De dónde vino el usuario, para que el enlace de volver lo devuelva ahí. */
/** Las cuatro secciones del panel lateral. Antes iban apiladas en un solo
 * scroll y habia que pasar por la ficha tecnica para llegar a los tags. */
type InfoTab = 'story' | 'file' | 'tags' | 'text';

const ORIGINS = {
  library: { label: 'Volver a la biblioteca', path: '/biblioteca' },
  archive: { label: 'Volver al archivo', path: '/archivo' },
  favorites: { label: 'Volver a favoritos', path: '/favoritos' },
  shared: { label: 'Volver a compartido', path: '/compartido' },
  places: { label: 'Volver a lugares', path: '/lugares' },
  home: { label: 'Volver al inicio', path: '/inicio' },
  explore: { label: 'Volver al inicio', path: '/inicio' },
  search: { label: 'Volver a la búsqueda', path: '/albumes' },
  // L11: la ruta real lleva el id del álbum inteligente (`smart_album_id`).
  smart: { label: 'Volver al álbum inteligente', path: '/albumes' },
} as const;

@Component({
  selector: 'app-media-detail',
  imports: [FormsModule, RouterLink, Icon, Modal, LocationPicker, MediaComments],
  templateUrl: './media-detail.html',
  styleUrl: './media-detail.css',
})
export class MediaDetail implements OnDestroy {
  private readonly api = inject(AlbumApi);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly confirm = inject(Confirm);
  /** Solo se pinta en `isPublic`: la vista autenticada ya tiene el interruptor
   * en el carril lateral, y no se duplican controles globales en el detalle. */
  readonly theme = inject(Theme);
  private readonly toast = inject(Toast);

  /** La lista de origen viaja en la URL, así que sobrevive a un refresco. */
  private readonly origin = ORIGINS[this.route.snapshot.queryParamMap.get('from') as keyof typeof ORIGINS];

  /**
   * Token del enlace público, cuando esta misma pantalla la abre un visitante
   * SIN sesión (`/enlace/:token/media/:mediaId`).
   *
   * Se reutiliza el detalle autenticado entero a propósito, en vez de escribir
   * un visor público aparte: es la única forma de que "se vea igual que dentro
   * de la cuenta" y siga viéndose igual cuando esta pantalla cambie. Lo único
   * que cambia es de dónde salen los datos; el rol llega fijo a 'read' sin
   * capacidades, así que TODOS los `can*()` que ya cierran las acciones de
   * escritura se apagan solos, sin una sola condición nueva en la plantilla.
   */
  readonly shareToken = this.route.snapshot.paramMap.get('token') ?? '';
  readonly isPublic = !!this.shareToken;
  /** Solo si el dueño lo permitió; en la vista autenticada siempre se puede. */
  readonly allowDownload = signal(!this.shareToken);

  /** El álbum desde el que se abrió (`/albumes/:albumId/media/:mediaId`), que
   * el servidor valida por pertenencia. `null` en `/recuerdos/:mediaId`, la
   * vista del recuerdo sin álbum de contexto (L10B). */
  readonly albumId: number | null = Number(this.route.snapshot.paramMap.get('albumId')) || null;

  readonly backLabel = this.isPublic
    ? 'Volver al álbum compartido'
    : (this.origin?.label ?? (this.albumId ? 'Volver al álbum' : 'Volver a la biblioteca'));
  mediaId = Number(this.route.snapshot.paramMap.get('mediaId'));
  private readonly navigationMedia = signal<Media[]>([]);
  private routeSubscription?: Subscription;
  private loadEpoch = 0;

  readonly album = signal<Album | null>(null);
  readonly media = signal<Media | null>(null);
  /** La política del enlace también decide qué pestañas existen: apagada,
   * solo quedan los píxeles, título y descripción, nunca una ficha vacía. */
  readonly showMetadata = signal(!this.shareToken);
  readonly metadata = signal<MediaMetadata | null>(null);
  readonly exif = signal<MediaExif | null>(null);
  readonly context = signal<MediaContext | null>(null);
  readonly enrichingContext = signal(false);
  readonly contextError = signal('');
  readonly ocr = signal<MediaOcr | null>(null);
  readonly ocrRunning = signal(false);
  readonly ocrError = signal('');
  /** El texto completo va en un diálogo con scroll propio: desplegarlo dentro
   * del panel empujaba el resto de la página con una foto de mucho texto. */
  readonly ocrOpen = signal(false);
  readonly zoomOpen = signal(false);
  /** Que pestana del panel esta abierta. Vive en memoria y no en la URL: al
   * recargar se vuelve a "Recuerdo", que es lo primero que se quiere ver. */
  readonly tab = signal<InfoTab>('story');
  /** Sugerencias de Imagga: viven solo en memoria hasta que el usuario elige.
   * Nada se crea ni se asigna sin que marque y confirme. */
  readonly suggestions = signal<TagSuggestion[]>([]);
  readonly chosenSuggestions = signal<ReadonlySet<string>>(new Set());
  readonly suggesting = signal(false);
  readonly addingSuggestions = signal(false);
  readonly suggestError = signal('');
  readonly tags = signal<Tag[]>([]);
  readonly allTags = signal<Tag[]>([]);
  /** Los álbumes que contienen este recuerdo. Solo los recibe su dueño: para
   * cualquier otro queda en `null` y la sección no existe. */
  readonly memberAlbums = signal<MediaAlbumRef[] | null>(null);
  readonly membershipBusy = signal(false);
  selectedAlbumToAdd = '';
  /** Los álbumes del dueño, para el selector de "Añadir a álbum". Se piden
   * todos (un `<select>` no tiene botón de "cargar más") y solo si es el dueño. */
  private readonly ownerAlbumPages = pagedList<Album>((page, perPage) =>
    this.api.albums({ role: 'owner', page: String(page), per_page: String(perPage) }));
  readonly role = signal<AlbumRole>('read');
  readonly capabilities = signal<AlbumCapability[]>([]);
  readonly assetUrl = signal('');
  /** v1.1: portada del video (su vista previa) mientras no se reproduce. */
  readonly videoPoster = signal('');
  readonly savingPoster = signal(false);
  private readonly player = viewChild<ElementRef<HTMLVideoElement>>('player');
  readonly loading = signal(true);
  readonly assetLoading = signal(true);
  readonly error = signal('');
  readonly newTagName = signal('');
  selectedTagId = '';

  readonly editOpen = signal(false);
  readonly editSaving = signal(false);
  readonly editError = signal('');
  editTitle = '';
  editCaption = '';
  /** Día y hora por separado: un `datetime-local` deja escribir una fecha a
   * medias y la entrega como cadena vacía, que ahora significa "vuelve a la
   * de la foto" — dos cosas distintas que no pueden compartir valor. */
  editDate = '';
  editTime = '';
  readonly editClearLocation = signal(false);
  private editLocation: { latitude: number; longitude: number } | null = null;

  /** "Apple iPhone 13". Vacio si la foto no dice con que se tomo. */
  readonly camera = computed(() => {
    const data = this.exif();
    const make = (data?.camera_make || '').trim();
    const model = (data?.camera_model || '').trim();
    // Canon y compania repiten la marca dentro del modelo ("Canon EOS 5D").
    if (model && make && model.toLowerCase().startsWith(make.toLowerCase())) return model;
    return [make, model].filter(Boolean).join(' ');
  });

  /** Coordenadas tal cual, que es lo que se pega en un buscador de mapas. */
  readonly coordinates = computed(() => {
    const data = this.exif();
    if (data?.latitude == null || data?.longitude == null) return '';
    return `${data.latitude.toFixed(6)}, ${data.longitude.toFixed(6)}`;
  });

  /** "4032 × 3024 · 12,2 MP". El panel de subida ya guarda la resolución: la
   * lee del propio archivo antes de subirlo, y hasta ahora no se enseñaba. */
  readonly resolution = computed(() => {
    const bruta = (this.metadata()?.resolution || '').trim();
    const m = /^(\d+)\s*[x×]\s*(\d+)$/i.exec(bruta);
    if (!m) return bruta;
    const [ancho, alto] = [Number(m[1]), Number(m[2])];
    const mp = (ancho * alto) / 1_000_000;
    return `${ancho} × ${alto}${mp >= 0.5 ? ` · ${mp.toFixed(1).replace('.', ',')} MP` : ''}`;
  });

  /** "12 m sobre el nivel del mar", solo si el EXIF la trae. */
  readonly altitude = computed(() => {
    const m = this.exif()?.altitude_m;
    return m == null ? '' : `${Math.round(m)} m sobre el nivel del mar`;
  });

  /** Favoritos y tags. Portada va aparte: escribe en el álbum. */
  readonly canManage = computed(() => canOrganizeMedia(this.role(), this.capabilities()));
  /** La portada es del álbum de contexto: sin álbum no hay portada que poner. */
  readonly canSetCover = computed(() => !!this.albumId && canEditAlbum(this.role(), this.capabilities()));
  /** Añadir y quitar de álbumes es solo del dueño, nunca una capacidad. */
  readonly canManageMemberships = computed(() => this.memberAlbums() !== null);
  readonly addableAlbums = computed(() => {
    const dentro = new Set((this.memberAlbums() ?? []).map(album => album.id));
    return this.ownerAlbumPages.items().filter(album => !dentro.has(album.id));
  });
  readonly canDelete = computed(() => canDeleteMedia(this.role(), this.capabilities()));
  readonly canEdit = computed(() => canEditMedia(this.role(), this.capabilities()));

  /** Las dos funciones que mandan la imagen a un tercero —detectar texto y
   * sugerir etiquetas— son del dueño y no de una capacidad: un colaborador
   * puede corregir datos de la foto, pero que los píxeles salgan del servidor
   * lo decide quien creó el álbum. */
  readonly canSendImageOut = computed(() => canManageAlbum(this.role()) && this.media()?.file_type === 'image');
  /** Ya existen en la biblioteca: reutilizarlas no crea nada y mantiene el
   * catálogo pequeño, así que van primero y en su propio grupo. */
  readonly knownSuggestions = computed(() => this.suggestions().filter(s => s.tag_id !== null));
  readonly newSuggestions = computed(() => this.suggestions().filter(s => s.tag_id === null));

  /** Las primeras líneas, para no llenar el panel con un texto largo. */
  readonly ocrPreview = computed(() => {
    const lines = (this.ocr()?.extracted_text || '').split('\n');
    return lines.length > 4 ? lines.slice(0, 4).join('\n') : '';
  });

  readonly hasGps = computed(() => !!this.coordinates());
  /** A diferencia de `hasGps` (solo EXIF), esto también cubre un lugar puesto
   * a mano: nunca se guarda la coordenada cruda de un pin manual, así que la
   * única señal de que existe es que `media_context` ya tiene fila. */
  readonly showContext = computed(() => !!this.context() || this.hasGps());

  /** El texto detectado solo existe para fotos, y la pestana solo tiene
   * sentido si ya hay texto o si este usuario puede pedirlo. */
  readonly showTextTab = computed(() => this.media()?.file_type === 'image' && (!!this.ocr() || this.canSendImageOut()));

  /** La pestana "Recuerdo" tiene algo que contar. Incluye `showContext()`
   * porque una foto con GPS todavia sin resolver tampoco esta vacia: puede
   * ofrecer el boton de completar. */
  readonly hasStory = computed(() => !!this.media()?.caption || !!this.media()?.taken_at || this.showContext());

  /** Punto de partida del selector al editar: solo se conoce si la foto trae
   * EXIF. Un pin puesto a mano no dejó rastro de sus coordenadas crudas, así
   * que el editor arranca en la vista del mundo, igual que al subir. */
  readonly editInitialLocation = computed(() => {
    const data = this.exif();
    if (data?.latitude == null || data?.longitude == null) return null;
    return { lat: data.latitude, lon: data.longitude };
  });

  /** "Guayaquil, Guayas, Ecuador". Vacio si aun no se completo el contexto. */
  readonly placeLabel = computed(() => {
    const ctx = this.context();
    if (!ctx) return '';
    return [ctx.locality, ctx.region, ctx.country_name].filter(Boolean).join(', ');
  });

  /** "El Consuelo": lo que responde a "dónde", en grande. */
  readonly placeName = computed(() => {
    const ctx = this.context();
    return ctx?.locality || ctx?.region || ctx?.country_name || '';
  });

  /** Lo que queda por encima del titular, sin repetirlo. Mismo criterio que PlaceCard. */
  readonly placeArea = computed(() => {
    const ctx = this.context();
    if (!ctx) return '';
    const resto = ctx.locality ? [ctx.region, ctx.country_name] : ctx.region ? [ctx.country_name] : [];
    return resto.filter(Boolean).join(', ');
  });

  /** "06:15" — hora de reloj, sin fecha: el día ya lo dice la ficha. */
  readonly sunriseLabel = computed(() => this.clock(this.context()?.sunrise_at));
  readonly sunsetLabel = computed(() => this.clock(this.context()?.sunset_at));
  readonly hasSolar = computed(() => !!(this.sunriseLabel() || this.sunsetLabel()));

  /**
   * "Cerca del atardecer" solo si la foto se tomó dentro de la ventana; nunca
   * se inventa "hora dorada", que no es un dato que dé el proveedor.
   */
  readonly solarMoment = computed(() => {
    const ctx = this.context();
    const takenAt = this.media()?.taken_at;
    if (!ctx || !takenAt) return '';
    const taken = new Date(takenAt).getTime();
    if (Number.isNaN(taken)) return '';
    const within = (value?: string | null) => {
      if (!value) return false;
      const moment = new Date(value).getTime();
      return !Number.isNaN(moment) && Math.abs(taken - moment) <= SOLAR_WINDOW_MS;
    };
    if (within(ctx.sunset_at)) return 'Cerca del atardecer';
    if (within(ctx.sunrise_at)) return 'Cerca del amanecer';
    return '';
  });

  /** "29 °C · Parcialmente nublado", con lo que haya de los dos. */
  readonly weatherLabel = computed(() => {
    const ctx = this.context();
    const temp = ctx?.weather_temp_c;
    return [temp == null ? '' : `${Math.round(temp)} °C`, ctx?.weather_condition || ''].filter(Boolean).join(' · ');
  });

  /** Lluvia y viento solo cuando dicen algo: "0 mm" en un día seco es ruido. */
  readonly weatherDetail = computed(() => {
    const ctx = this.context();
    const partes: string[] = [];
    if (ctx?.weather_precip_mm) partes.push(`${ctx.weather_precip_mm} mm lluvia`);
    if (ctx?.weather_wind_kph) partes.push(`${Math.round(ctx.weather_wind_kph)} km/h viento`);
    return partes.join(' · ');
  });

  /** Queda algo por completar, así que el botón tiene sentido. Un festivo ya
   * consultado que resultó no serlo cuenta como completo (`*_enriched_at`). */
  readonly contextIncomplete = computed(() => {
    const ctx = this.context();
    if (!ctx) return true;
    const conFecha = !!this.media()?.taken_at;
    return (conFecha && !ctx.solar_enriched_at)
      || (conFecha && !!ctx.country_code && !ctx.holiday_enriched_at)
      || !ctx.weather_enriched_at;
  });

  private clock(value?: string | null) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('es', { hour: '2-digit', minute: '2-digit' }).format(date);
  }

  ngOnInit() {
    this.loadNavigationMedia();
    this.loadCurrentMedia();
    this.routeSubscription = this.route.paramMap.subscribe(params => {
      const requestedId = Number(params.get('mediaId'));
      if (requestedId === this.mediaId) return;
      this.mediaId = requestedId;
      this.loadCurrentMedia();
    });
  }

  private loadNavigationMedia() {
    if (this.isPublic) {
      this.api.sharedLink(this.shareToken).subscribe({ next: response => this.navigationMedia.set(response.data.media) });
      return;
    }
    if (this.albumId) {
      this.api.media(this.albumId).subscribe({ next: response => this.navigationMedia.set(response.data) });
    }
  }

  private loadCurrentMedia() {
    const epoch = ++this.loadEpoch;
    this.revokeAsset();
    this.media.set(null);
    this.metadata.set(null);
    this.exif.set(null);
    this.context.set(null);
    this.ocr.set(null);
    this.tags.set([]);
    this.memberAlbums.set(null);
    this.showMetadata.set(!this.isPublic);
    this.enrichingContext.set(false);
    this.ocrRunning.set(false);
    this.suggesting.set(false);
    this.addingSuggestions.set(false);
    this.error.set('');
    this.loading.set(true);
    this.assetLoading.set(true);
    if (!Number.isInteger(this.mediaId) || this.mediaId <= 0) {
      this.loading.set(false);
      this.error.set('La dirección de este recuerdo no es válida.');
      return;
    }
    if (this.isPublic) return this.loadPublic(epoch);

    if (this.albumId) {
      this.api.album(this.albumId).subscribe({
        next: response => { if (epoch === this.loadEpoch) this.album.set(response.data); },
        error: error => { if (epoch === this.loadEpoch) this.fail(error, 'No se pudo abrir el álbum.'); },
      });
    }

    this.api.mediaDetail(this.mediaId, this.albumId).subscribe({
      next: response => {
        if (epoch !== this.loadEpoch) return;
        if (this.albumId && response.data.media.album_id !== this.albumId) {
          this.loading.set(false);
          this.error.set('Este recuerdo no pertenece al álbum indicado.');
          return;
        }
        this.media.set(response.data.media);
        this.metadata.set(response.data.metadata || null);
        this.exif.set(response.data.exif || null);
        this.tags.set(response.data.tags || []);
        this.role.set(response.data.album_role);
        this.capabilities.set(response.data.album_capabilities || []);
        this.memberAlbums.set(response.data.albums ?? null);
        this.loading.set(false);
        if (this.canManageMemberships() && !this.ownerAlbumPages.loaded()) this.ownerAlbumPages.loadAll();
        if (this.canManage()) {
          this.api.tags('', this.albumId ?? undefined).subscribe({ next: tags => { if (epoch === this.loadEpoch) this.allTags.set(tags.data); } });
        }
        // Se pide siempre, no solo con EXIF: un lugar puesto a mano en el
        // mapa también resuelve contexto y no deja rastro en el EXIF.
        this.api.mediaContext(this.mediaId).subscribe({ next: r => { if (epoch === this.loadEpoch) this.context.set(r.data.context); } });
        if (response.data.media.file_type === 'image') {
          this.api.mediaOcr(this.mediaId).subscribe({ next: r => { if (epoch === this.loadEpoch) this.ocr.set(r.data.ocr); } });
        }
        this.loadAsset(epoch);
      },
      error: error => { if (epoch === this.loadEpoch) this.fail(error, 'No se pudo cargar este recuerdo.'); },
    });
  }

  /**
   * Mismo detalle, pero pedido con el token del enlace en vez de con sesión.
   * Todo llega en UNA respuesta (contexto y OCR incluidos), y el rol viene
   * fijado a 'read' por el servidor -- no se toca aquí, para que no exista un
   * camino en el cliente capaz de conceder escritura a un visitante.
   */
  private loadPublic(epoch: number) {
    this.api.sharedMediaDetail(this.shareToken, this.mediaId).subscribe({
      next: response => {
        if (epoch !== this.loadEpoch) return;
        const data = response.data;
        this.media.set(data.media);
        this.metadata.set(data.metadata || null);
        this.exif.set(data.exif || null);
        this.tags.set(data.tags || []);
        this.context.set(data.context || null);
        this.ocr.set(data.ocr || null);
        this.role.set(data.album_role);
        this.capabilities.set(data.album_capabilities || []);
        this.allowDownload.set(!!data.allow_original_download);
        this.showMetadata.set(!!data.show_metadata);
        this.loading.set(false);
        this.loadAsset(epoch);
      },
      error: error => { if (epoch === this.loadEpoch) this.fail(error, 'No se pudo cargar este recuerdo.'); },
    });
  }

  ngOnDestroy() {
    this.routeSubscription?.unsubscribe();
    this.revokeAsset();
  }

  previousMediaId() { return neighboringMediaId(this.navigationMedia(), this.mediaId, 'previous'); }
  nextMediaId() { return neighboringMediaId(this.navigationMedia(), this.mediaId, 'next'); }
  hasOpenDialog() { return this.editOpen() || this.ocrOpen() || this.zoomOpen(); }
  private isCurrentMedia(mediaId: number, epoch: number) {
    return isCurrentMediaRequest(mediaId, epoch, this.mediaId, this.loadEpoch);
  }

  navigateMedia(direction: MediaDirection) {
    if (this.hasOpenDialog()) return;
    const nextId = neighboringMediaId(this.navigationMedia(), this.mediaId, direction);
    if (!nextId) return;
    if (this.isPublic) return void this.router.navigate(['/enlace', this.shareToken, 'media', nextId]);
    this.router.navigate(mediaDetailLink(this.albumId, nextId), { queryParams: this.route.snapshot.queryParams });
  }

  @HostListener('document:keydown', ['$event'])
  onNavigationKey(event: KeyboardEvent) {
    const direction = navigationKey(event.key, event.target, this.hasOpenDialog());
    if (!direction || !neighboringMediaId(this.navigationMedia(), this.mediaId, direction)) return;
    event.preventDefault();
    this.navigateMedia(direction);
  }

  back() {
    if (this.isPublic) return void this.router.navigate(['/enlace', this.shareToken]);
    if (this.origin === ORIGINS.smart) {
      const smartId = Number(this.route.snapshot.queryParamMap.get('smart_album_id'));
      return void this.router.navigate(Number.isInteger(smartId) && smartId > 0 ? ['/albumes/inteligentes', smartId] : ['/albumes']);
    }
    if (this.origin) {
      // La búsqueda viaja en la URL del detalle, así que volver reconstruye
      // los resultados en vez de dejar al usuario en la lista sin filtrar.
      const queryParams = this.origin === ORIGINS.search
        ? advancedSearchToParams(advancedSearchFromParams(this.route.snapshot.queryParamMap))
        : this.origin === ORIGINS.library || this.origin === ORIGINS.archive
          ? { focus: String(this.mediaId) }
          : {};
      return void this.router.navigate([this.origin.path], { queryParams });
    }
    this.router.navigate(this.albumId ? ['/albumes', this.albumId] : ['/biblioteca']);
  }

  /** Mete el recuerdo en otro álbum del dueño, sin volver a subir nada. */
  addToAlbum() {
    const item = this.media();
    const albumId = Number(this.selectedAlbumToAdd);
    const album = this.addableAlbums().find(candidate => candidate.id === albumId);
    if (!item || !album || !this.canManageMemberships() || this.membershipBusy()) return;
    const epoch = this.loadEpoch;
    this.membershipBusy.set(true);
    this.api.addToAlbum(album.id, item.id).subscribe({
      next: () => {
        this.membershipBusy.set(false);
        if (!this.isCurrentMedia(item.id, epoch)) return;
        this.memberAlbums.update(current => [...(current ?? []), { id: album.id, titulo: album.titulo }]);
        this.selectedAlbumToAdd = '';
        this.toast.success(`Añadido a «${album.titulo}».`);
      },
      error: error => {
        this.membershipBusy.set(false);
        if (this.isCurrentMedia(item.id, epoch)) this.toast.error(error?.error?.message || 'No se pudo añadir al álbum.');
      },
    });
  }

  /** Quitar de un álbum no borra nada: el recuerdo sigue en la biblioteca y en
   * sus otros álbumes. Distinto de "Enviar a papelera", que es global. */
  async removeFromAlbum(album: MediaAlbumRef) {
    const item = this.media();
    if (!item || !this.canManageMemberships() || this.membershipBusy()) return;
    const epoch = this.loadEpoch;
    const otros = (this.memberAlbums() ?? []).length > 1;
    const confirmed = await this.confirm.ask({
      title: 'Quitar del álbum',
      message: `Se quita de «${album.titulo}». El recuerdo no se borra: sigue en tu biblioteca${otros ? ' y en sus otros álbumes' : ''}.`,
      confirmLabel: 'Quitar del álbum',
    });
    if (!confirmed || !this.isCurrentMedia(item.id, epoch)) return;
    this.membershipBusy.set(true);
    this.api.removeFromAlbum(album.id, item.id).subscribe({
      next: () => {
        this.membershipBusy.set(false);
        if (!this.isCurrentMedia(item.id, epoch)) return;
        this.toast.success(`Quitado de «${album.titulo}».`);
        // El álbum de contexto ya no lo contiene: la vista pasa a la del recuerdo.
        if (album.id === this.albumId) {
          return void this.router.navigate(mediaDetailLink(null, item.id), { queryParams: this.route.snapshot.queryParams, replaceUrl: true });
        }
        this.memberAlbums.update(current => (current ?? []).filter(candidate => candidate.id !== album.id));
      },
      error: error => {
        this.membershipBusy.set(false);
        if (this.isCurrentMedia(item.id, epoch)) this.toast.error(error?.error?.message || 'No se pudo quitar del álbum.');
      },
    });
  }

  toggleFavorite() {
    const item = this.media();
    if (!item || !this.canManage()) return;
    const mediaId = item.id;
    const epoch = this.loadEpoch;
    this.api.favorite(mediaId, !item.is_favorite).subscribe({
      next: response => { if (this.isCurrentMedia(mediaId, epoch)) this.media.update(current => current ? { ...current, is_favorite: response.data.is_favorite } : current); },
      error: error => { if (this.isCurrentMedia(mediaId, epoch)) this.toast.error(error?.error?.message || 'No se pudo actualizar el favorito.'); },
    });
  }

  /** Archivar saca el recuerdo de Biblioteca e Inicio sin borrarlo (P09). */
  toggleArchive() {
    const item = this.media();
    if (!item || !this.canManage()) return;
    const mediaId = item.id;
    const epoch = this.loadEpoch;
    const archived = !item.archived_at;
    this.api.archive(mediaId, archived).subscribe({
      next: response => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        this.media.update(current => current ? { ...current, archived_at: response.data.archived_at } : current);
        this.toast.success(archived ? 'Archivado: ya no aparece en Biblioteca ni en Inicio.' : 'Desarchivado.');
      },
      error: error => { if (this.isCurrentMedia(mediaId, epoch)) this.toast.error(error?.error?.message || 'No se pudo archivar.'); },
    });
  }

  /**
   * Un solo botón para todo el contexto: lugar, sol, festivo y clima. Antes
   * era uno por proveedor, y eso dejaba huecos sin salida — una foto con el
   * sol ya revelado no tenía forma de pedir el festivo, porque su botón solo
   * aparecía cuando faltaba el sol.
   */
  completeContext() {
    if (!this.canEdit() || this.enrichingContext()) return;
    const mediaId = this.mediaId;
    const epoch = this.loadEpoch;
    this.contextError.set('');
    this.enrichingContext.set(true);
    this.api.enrichContext(mediaId).subscribe({
      next: response => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        this.enrichingContext.set(false);
        this.context.set(response.data.context);
        const message = this.describeResults(response.data.results);
        if (message) this.contextError.set(message);
      },
      error: error => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        this.enrichingContext.set(false);
        this.contextError.set(error?.error?.message || 'No pudimos completar el contexto.');
      },
    });
  }

  /** Manda una copia reducida de la foto a OCR.Space. Nunca corre solo: es la
   * única función que saca los píxeles del servidor, así que la dispara el
   * dueño a mano, después de leer el aviso. */
  detectText() {
    if (!this.canSendImageOut() || this.ocrRunning()) return;
    const mediaId = this.mediaId;
    const epoch = this.loadEpoch;
    this.ocrError.set('');
    this.ocrRunning.set(true);
    this.api.detectOcr(mediaId).subscribe({
      next: response => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        this.ocrRunning.set(false);
        this.ocr.set(response.data.ocr);
        this.ocrOpen.set(false);
        if (!response.data.ocr) this.ocrError.set('No encontramos texto en esta foto.');
      },
      error: error => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        this.ocrRunning.set(false);
        this.ocrError.set(error?.error?.message || 'No pudimos analizar la imagen.');
      },
    });
  }

  /** Pide sugerencias a Imagga. No crea nada: solo llena la lista para elegir. */
  suggestTags() {
    if (!this.canSendImageOut() || this.suggesting()) return;
    const mediaId = this.mediaId;
    const epoch = this.loadEpoch;
    this.suggestError.set('');
    this.suggesting.set(true);
    this.api.suggestTags(mediaId).subscribe({
      next: response => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        this.suggesting.set(false);
        const puestas = new Set(this.tags().map(tag => tag.name.toLowerCase()));
        const nuevas = response.data.suggestions.filter(s => !puestas.has(s.name));
        this.suggestions.set(nuevas);
        this.chosenSuggestions.set(new Set());
        if (!nuevas.length) this.suggestError.set('No encontramos etiquetas nuevas para esta foto.');
      },
      error: error => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        this.suggesting.set(false);
        this.suggestError.set(error?.error?.message || 'No pudimos sugerir etiquetas ahora.');
      },
    });
  }

  toggleSuggestion(name: string) {
    this.chosenSuggestions.update(current => {
      const siguiente = new Set(current);
      siguiente.has(name) ? siguiente.delete(name) : siguiente.add(name);
      return siguiente;
    });
  }

  /**
   * Confirma las marcadas por el camino de siempre: crea las que no existan y
   * asigna todas con los endpoints de tags que ya usaba el formulario manual.
   */
  addChosenSuggestions() {
    const elegidas = [...this.chosenSuggestions()];
    if (!elegidas.length || this.addingSuggestions()) return;
    const mediaId = this.mediaId;
    const epoch = this.loadEpoch;
    this.suggestError.set('');
    this.addingSuggestions.set(true);

    // Una sugerencia conocida ya viene con su `tag_id` del servidor; el
    // catálogo local solo cubre el caso raro de que se creara en otra pestaña.
    const catalogo = new Map(this.allTags().map(tag => [tag.name.toLowerCase(), tag]));
    const porNombre = new Map(this.suggestions().map(s => [s.name, s]));
    const existentes: Tag[] = [];
    const porCrear: string[] = [];
    for (const name of elegidas) {
      const conocida = porNombre.get(name);
      const local = catalogo.get(name);
      if (conocida?.tag_id != null) existentes.push({ id: conocida.tag_id, name });
      else if (local) existentes.push(local);
      else porCrear.push(name);
    }

    const creaciones = porCrear.length
      ? forkJoin(porCrear.map(name => this.api.createTag(name, this.albumId ?? undefined)))
      : of([] as { data: Tag }[]);

    creaciones.subscribe({
      next: creadas => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        const todas = [...existentes, ...creadas.map(r => r.data)];
        this.allTags.update(tags => todas.reduce((acc, tag) => upsertTagSorted(acc, tag), tags));
        this.api.assignTags(mediaId, todas.map(tag => tag.id)).subscribe({
          next: () => {
            if (!this.isCurrentMedia(mediaId, epoch)) return;
            this.addingSuggestions.set(false);
            this.tags.update(tags => todas.reduce((acc, tag) => upsertTagSorted(acc, tag), tags));
            this.suggestions.update(lista => lista.filter(s => !this.chosenSuggestions().has(s.name)));
            this.chosenSuggestions.set(new Set());
          },
          error: error => {
            if (!this.isCurrentMedia(mediaId, epoch)) return;
            this.addingSuggestions.set(false);
            this.suggestError.set(error?.error?.message || 'No se pudieron asignar las etiquetas.');
          },
        });
      },
      error: error => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        this.addingSuggestions.set(false);
        this.suggestError.set(error?.error?.message || 'No se pudieron crear las etiquetas.');
      },
    });
  }

  async removeText() {
    if (!this.canSendImageOut() || !this.ocr()) return;
    const mediaId = this.mediaId;
    const epoch = this.loadEpoch;
    const confirmed = await this.confirm.ask({
      title: 'Eliminar el texto detectado',
      message: 'La foto no cambia. Solo se borra el texto extraído, y dejará de aparecer en las búsquedas.',
      confirmLabel: 'Eliminar',
      danger: true,
    });
    if (!confirmed) return;
    if (!this.isCurrentMedia(mediaId, epoch)) return;
    this.ocrError.set('');
    this.api.deleteOcr(mediaId).subscribe({
      next: () => { if (this.isCurrentMedia(mediaId, epoch)) { this.ocr.set(null); this.ocrOpen.set(false); } },
      error: error => { if (this.isCurrentMedia(mediaId, epoch)) this.ocrError.set(error?.error?.message || 'No pudimos eliminar el texto detectado.'); },
    });
  }

  /**
   * Solo se avisa de lo que el usuario podría arreglar o esperar. Que un
   * proveedor no esté configurado, o que ese día no fuera festivo, no es
   * algo que deba leer: se nota en que la línea no aparece.
   */
  private describeResults(results: ContextResults) {
    if (results.weather === 'quota_exhausted') return 'El contexto de clima alcanzó su cuota gratuita de hoy. Vuelve mañana.';
    if (results.location === 'missing_gps') return 'Esta foto no tiene coordenadas. Añade un lugar para completar su contexto.';
    if (results.solar === 'missing_date' || results.holiday === 'missing_date') return 'Añade la fecha de captura para ver el amanecer, el atardecer y los festivos.';
    const caidos = ['location', 'solar', 'holiday', 'weather']
      .filter(k => ['unavailable', 'quota'].includes((results as Record<string, string | undefined>)[k] || ''));
    return caidos.length ? 'Algunos datos no se pudieron consultar ahora. Puedes intentarlo de nuevo más tarde.' : '';
  }

  openEdit() {
    const item = this.media();
    if (!item || !this.canEdit()) return;
    this.editTitle = item.title || '';
    this.editCaption = item.caption || '';
    this.editDate = item.taken_at ? item.taken_at.slice(0, 10) : '';
    this.editTime = item.taken_at ? item.taken_at.slice(11, 16) : '';
    this.editLocation = null;
    this.editClearLocation.set(false);
    this.editError.set('');
    this.editOpen.set(true);
  }

  /** Solo tiene sentido ofrecer "quitar lugar" si hay uno puesto. */
  readonly canRemoveLocation = computed(() => !!this.context());

  toggleClearLocation() {
    this.editClearLocation.update(current => !current);
    if (this.editClearLocation()) this.editLocation = null;
  }

  closeEdit() {
    if (this.editSaving()) return;
    this.editOpen.set(false);
  }

  /** El pin solo viaja en el guardado si el usuario lo tocó en esta sesión
   * de edición: no re-lanzar la geocodificación de un lugar que ya estaba bien. */
  onEditLocationPicked(point: { latitude: number; longitude: number }) {
    this.editLocation = point;
  }

  saveEdit() {
    if (this.editSaving()) return;
    const mediaId = this.mediaId;
    const epoch = this.loadEpoch;
    this.editError.set('');
    this.editSaving.set(true);

    const payload: MediaUpdatePayload = {
      title: this.editTitle.trim() || null,
      caption: this.editCaption.trim() || null,
      // Sin día no se manda hora: una hora suelta no es una fecha.
      taken_at: this.editDate ? (this.editTime ? `${this.editDate}T${this.editTime}` : this.editDate) : null,
    };
    if (this.editClearLocation()) {
      payload.clear_location = true;
    } else if (this.editLocation) {
      payload.latitude = this.editLocation.latitude;
      payload.longitude = this.editLocation.longitude;
    }

    this.api.updateMedia(mediaId, payload).subscribe({
      next: response => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        this.editSaving.set(false);
        this.editOpen.set(false);
        this.media.update(current => current ? { ...current, ...response.data } : current);
        this.toast.success('Foto actualizada');
        // El contexto pudo cambiar de sitio, de día (sol y festivo) o
        // desaparecer: se recarga siempre que se haya tocado la ubicación.
        if (this.editLocation || this.editClearLocation()) {
          this.api.mediaContext(mediaId).subscribe({ next: r => { if (this.isCurrentMedia(mediaId, epoch)) this.context.set(r.data.context); } });
        }
      },
      error: error => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        this.editSaving.set(false);
        this.editError.set(error?.error?.message || 'No se pudo guardar los cambios.');
      },
    });
  }

  setCover() {
    const item = this.media();
    if (!item || !this.canSetCover()) return;
    if (!this.albumId) return;
    this.api.updateAlbum(this.albumId, { cover_media_id: item.id }).subscribe({
      next: response => { this.album.set(response.data); this.toast.success('Portada actualizada.'); },
      error: error => this.toast.error(error?.error?.message || 'No se pudo cambiar la portada.'),
    });
  }

  /** Descarga el archivo original: reutiliza el blob que el visor ya cargo
   * en `assetUrl()`, sin pedirlo dos veces ni depender de un endpoint nuevo. */
  downloadMedia() {
    const item = this.media();
    if (!item || !this.allowDownload()) return;
    // En público lo que se ve es la vista previa, así que descargar el
    // original es una petición aparte -- reutilizar el blob del visor
    // entregaría el derivado en vez del archivo que el dueño autorizó.
    if (this.isPublic) {
      return void this.api.sharedMediaFile(this.shareToken, item.id).subscribe({
        next: blob => this.saveBlob(blob, item),
        error: error => this.toast.error(error?.error?.message || 'No se pudo descargar el original.'),
      });
    }
    const url = this.assetUrl();
    if (!url) return;
    this.triggerDownload(url, item);
  }

  private saveBlob(blob: Blob, item: Media) {
    const url = URL.createObjectURL(blob);
    this.triggerDownload(url, item);
    URL.revokeObjectURL(url);
  }

  private triggerDownload(url: string, item: Media) {
    const a = document.createElement('a');
    a.href = url;
    a.download = this.metadata()?.original_filename || item.title || `archivo-${item.id}`;
    a.click();
  }

  async deleteMedia() {
    const item = this.media();
    if (!item || !this.canDelete()) return;
    const mediaId = item.id;
    const epoch = this.loadEpoch;

    const confirmed = await this.confirm.ask({
      title: 'Enviar a la papelera',
      message: 'Se envía a la papelera y desaparece de todos sus álbumes hasta que se restaure. Podrá recuperarse durante 30 días.',
      confirmLabel: 'Enviar a la papelera',
      danger: true,
    });
    if (!confirmed) return;
    if (!this.isCurrentMedia(mediaId, epoch)) return;

    this.api.deleteMedia(mediaId).subscribe({
      next: () => { if (this.isCurrentMedia(mediaId, epoch)) this.back(); },
      error: error => { if (this.isCurrentMedia(mediaId, epoch)) this.toast.error(error?.error?.message || 'No se pudo eliminar el archivo.'); },
    });
  }

  assignTag() {
    const tagId = Number(this.selectedTagId);
    if (!this.canManage() || !tagId || this.tags().some(tag => tag.id === tagId)) return;
    const mediaId = this.mediaId;
    const epoch = this.loadEpoch;
    this.api.assignTags(mediaId, [tagId]).subscribe({
      next: () => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        const tag = this.allTags().find(current => current.id === tagId);
        if (tag) this.tags.update(tags => upsertTagSorted(tags, tag));
        this.selectedTagId = '';
      },
      error: error => { if (this.isCurrentMedia(mediaId, epoch)) this.toast.error(error?.error?.message || 'No se pudo asignar el tag.'); },
    });
  }

  createTag() {
    const name = this.newTagName().trim();
    if (!this.canManage() || !name) return;
    const mediaId = this.mediaId;
    const epoch = this.loadEpoch;
    this.api.createTag(name, this.albumId ?? undefined).subscribe({
      next: response => {
        if (!this.isCurrentMedia(mediaId, epoch)) return;
        const tag = response.data;
        this.allTags.update(tags => upsertTagSorted(tags, tag));
        this.api.assignTags(mediaId, [tag.id]).subscribe({
          next: () => {
            if (!this.isCurrentMedia(mediaId, epoch)) return;
            this.tags.update(tags => upsertTagSorted(tags, tag));
            this.newTagName.set('');
          },
          error: error => { if (this.isCurrentMedia(mediaId, epoch)) this.toast.error(error?.error?.message || 'No se pudo asignar el tag.'); },
        });
      },
      error: error => { if (this.isCurrentMedia(mediaId, epoch)) this.toast.error(error?.error?.message || 'No se pudo crear el tag.'); },
    });
  }

  removeTag(tag: Tag) {
    if (!this.canManage()) return;
    const mediaId = this.mediaId;
    const epoch = this.loadEpoch;
    this.api.unassignTag(mediaId, tag.id).subscribe({
      next: () => { if (this.isCurrentMedia(mediaId, epoch)) this.tags.update(tags => tags.filter(current => current.id !== tag.id)); },
      error: error => { if (this.isCurrentMedia(mediaId, epoch)) this.toast.error(error?.error?.message || 'No se pudo quitar el tag.'); },
    });
  }

  formatBytes(value?: number | null) {
    if (value == null || !Number.isFinite(value)) return 'No disponible';
    if (value < 1024) return `${value} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let size = value / 1024;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
    return `${size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${units[unit]}`;
  }


  /** "2 min 05 s". Vuelve porque la Ficha ya enseña la duración de un video;
   * se había borrado cuando se quedó sin ningún consumidor. */
  formatDuration(value?: number | null) {
    if (value == null || !Number.isFinite(value)) return 'No disponible';
    const minutos = Math.floor(value / 60);
    const segundos = Math.floor(value % 60);
    return minutos ? `${minutos} min ${segundos.toString().padStart(2, '0')} s` : `${segundos} s`;
  }

  /** "27 ago 2026, 15:24". La ficha es una rejilla estrecha de dos columnas y
   * la fecha larga la descuadra envolviendo a dos lineas. */
  formatDateShort(value?: string | null) {
    if (!value) return 'No disponible';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'No disponible';
    return new Intl.DateTimeFormat('es', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
  }

  formatDate(value?: string | null) {
    if (!value) return 'No disponible';
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
    const date = dateOnly ? new Date(`${value}T12:00:00`) : new Date(value);
    if (Number.isNaN(date.getTime())) return 'No disponible';
    return new Intl.DateTimeFormat('es', dateOnly
      ? { dateStyle: 'long' }
      : { dateStyle: 'long', timeStyle: 'short' }
    ).format(date);
  }


  private loadAsset(epoch: number) {
    const mediaId = this.mediaId;
    // v1.1: un video autenticado se reproduce desde su URL directa. El
    // navegador pide rangos (206) y empieza a reproducir sin bajar el
    // archivo entero; cada rango pasa por la misma autorización de siempre.
    // Antes se descargaba ENTERO como Blob antes de poder darle a play.
    if (!this.isPublic && this.media()?.file_type === 'video') {
      this.revokeAsset();
      this.assetUrl.set(this.api.mediaFileUrl(mediaId));
      this.assetLoading.set(false);
      this.api.mediaPreview(mediaId).subscribe({
        next: blob => { if (epoch === this.loadEpoch) this.setPoster(blob); },
        error: () => undefined,
      });
      return;
    }
    this.assetLoading.set(true);
    // En un enlace público SIEMPRE se pinta la vista previa (derivado sin
    // EXIF), nunca el original: ver y descargar siguen siendo dos permisos
    // distintos, igual que en la cuadrícula.
    const source = this.isPublic
      ? this.api.sharedMediaPreview(this.shareToken, mediaId)
      : this.api.mediaFile(mediaId);
    source.subscribe({
      next: blob => {
        if (epoch !== this.loadEpoch) return;
        this.revokeAsset();
        this.assetUrl.set(URL.createObjectURL(blob));
        this.assetLoading.set(false);
      },
      error: error => {
        if (epoch !== this.loadEpoch) return;
        this.assetLoading.set(false);
        this.toast.error(error?.error?.message || 'El archivo no pudo cargarse desde el almacenamiento.');
      },
    });
  }

  private revokeAsset() {
    const current = this.assetUrl();
    if (current.startsWith('blob:')) URL.revokeObjectURL(current);
    this.assetUrl.set('');
    this.setPoster(null);
  }

  private setPoster(blob: Blob | null) {
    const old = this.videoPoster();
    if (old) URL.revokeObjectURL(old);
    this.videoPoster.set(blob ? URL.createObjectURL(blob) : '');
  }

  /** v1.1: el fotograma que se ve en el reproductor pasa a ser la portada. */
  async useFrameAsPoster() {
    const video = this.player()?.nativeElement;
    const item = this.media();
    if (!video || !item || item.file_type !== 'video' || !this.canEdit() || this.savingPoster()) return;
    video.pause();
    this.savingPoster.set(true);
    try {
      const blob = await captureFrame(video, video.currentTime);
      this.api.setVideoPoster(item.id, blob).subscribe({
        next: () => { this.setPoster(blob); this.toast.success('Portada del video actualizada.'); this.savingPoster.set(false); },
        error: error => { this.toast.error(error?.error?.message || 'No se pudo guardar la portada.'); this.savingPoster.set(false); },
      });
    } catch {
      this.toast.error('No pudimos capturar este fotograma en este navegador.');
      this.savingPoster.set(false);
    }
  }

  private fail(error: any, fallback: string) {
    this.loading.set(false);
    this.error.set(error?.error?.message || fallback);
  }
}
