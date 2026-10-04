import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { pagedList } from '../../../core/utils/paged-list';
import { AlbumActivity } from '../../../components/ui/album-activity/album-activity';
import { MediaGrid } from '../../../components/ui/media-grid/media-grid';
import { Modal } from '../../../components/ui/modal/modal';
import { UploadPanel } from '../../../components/ui/upload-panel/upload-panel';
import { Icon } from '../../../components/ui/icon/icon';
import { ShareQr } from '../../../components/ui/share-qr/share-qr';
import { ScrollReveal } from '../../../core/directives/scroll-reveal';
import { ALBUM_CAPABILITIES, AlbumCapability, CAPABILITY_HINTS, CAPABILITY_LABELS, canDeleteMedia, canEditAlbum, canManageAlbum, canOrganizeMedia, canUploadMedia } from '../../../core/models/album-permissions';
import { Album, AlbumApi, Media, Share, SharePermission, Tag } from '../../../core/services/album-api';
import { Confirm } from '../../../core/services/confirm';
import { Toast } from '../../../core/services/toast';
import { copyToClipboard } from '../../../core/utils/clipboard';
import { upsertTagSorted } from '../../../core/utils/tag-catalog';
import { publicLinkOptions } from '../../../core/utils/public-link-options';

/** Opciones del selector de orden, traducidas a los parámetros de la API. */
const SORT_OPTIONS = {
  'created_at:desc': { label: 'Subida, más recientes', sort_by: 'created_at', sort_dir: 'desc' },
  'created_at:asc': { label: 'Subida, más antiguas', sort_by: 'created_at', sort_dir: 'asc' },
  'taken_at:desc': { label: 'Captura, más recientes', sort_by: 'taken_at', sort_dir: 'desc' },
  'taken_at:asc': { label: 'Captura, más antiguas', sort_by: 'taken_at', sort_dir: 'asc' },
} as const;

type SortKey = keyof typeof SORT_OPTIONS;

/** Las tres pestañas del diálogo "Compartir álbum" -- antes eran tres
 * botones de la barra ("Compartir"/"Colaboradores"/"Enlaces") que abrían
 * tres modales por separado. Mismo problema y misma solución que ya
 * resolvió MediaDetail (Recuerdo/Ficha/Tags/Texto): un solo panel con
 * pestañas en vez de varias superficies de nivel superior. */
type ShareTab = 'new' | 'collaborators' | 'links';

const PERMISSION_LABELS: Record<SharePermission, string> = { read: 'Solo lectura', write: 'Colaborador' };

/** De donde se puede llegar a un album aparte de "Mis albumes". El perfil es
 * dinamico (lleva el username) y se resuelve aparte en resolveBackLink().
 * Mismo patron que ORIGINS en MediaDetail. */
const ORIGINS = {
  shared: { label: 'Volver a compartido', path: '/compartido' },
  home: { label: 'Volver al inicio', path: '/inicio' },
} as const;

@Component({
  selector: 'app-album-detail',
  imports: [RouterLink, AlbumActivity, MediaGrid, UploadPanel, Modal, FormsModule, DatePipe, Icon, ScrollReveal, ShareQr],
  templateUrl: './album-detail.html',
  styleUrl: './album-detail.css',
})
export class AlbumDetail {
  private readonly api = inject(AlbumApi);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly confirm = inject(Confirm);
  private readonly toast = inject(Toast);

  id = Number(this.route.snapshot.paramMap.get('id'));

  /** La lista de origen viaja en la URL, asi que "volver" regresa ahi en vez
   * de siempre a Mis albumes (donde un album compartido o de otro perfil ni
   * siquiera aparece). */
  private readonly backLink = this.resolveBackLink();
  readonly backLabel = this.backLink.label;
  readonly backPath = this.backLink.path;

  album = signal<Album | null>(null);

  /** P01: la media se carga por paginas y se APILA. Antes se pedia
   *  `per_page=100` una sola vez y se hacia `.set(response.data)`, asi que la
   *  foto 101 de un album no existia para el usuario: no habia ninguna forma
   *  de llegar a ella desde la interfaz. */
  private readonly pages = pagedList<Media>((page, perPage) => {
    const sort = SORT_OPTIONS[this.sortKey];
    const params: Record<string, string> = {
      page: String(page),
      per_page: String(perPage),
      sort_by: sort.sort_by,
      sort_dir: sort.sort_dir,
    };
    if (this.filterType) params['file_type'] = this.filterType;
    if (this.filterTag) params['tag_id'] = this.filterTag;
    if (this.filterFavorite) params['is_favorite'] = this.filterFavorite;
    if (this.filterPlace.trim()) params['place'] = this.filterPlace.trim();
    return this.api.media(this.id, params);
  });

  readonly media = this.pages.items;
  /** El total que dice el servidor, no lo que llevamos cargado. */
  readonly totalMedia = this.pages.total;
  readonly loadingMore = this.pages.loading;
  readonly mediaError = this.pages.error;
  hasMore() { return this.pages.hasMore(); }
  loadMore() { this.pages.loadMore(); }

  shares = signal<Share[]>([]);
  tags = signal<Tag[]>([]);
  selectedMedia = signal<Media | null>(null);
  selectedMediaTags = signal<Tag[]>([]);
  copiedLink = signal('');
  error = signal('');
  readonly loaded = this.pages.loaded;

  uploading = signal(false);
  private uploadedInPanel = 0;
  sharing = signal(false);
  editing = signal(false);
  /** L12: el panel de actividad. Solo existe mientras está abierto, así que
   * cada apertura vuelve a pedir el historial. */
  showingActivity = signal(false);
  savingSettings = signal(false);
  downloadingAlbum = signal(false);

  /** Paso 2 de compartir: elegir qué podrá hacer el colaborador. Abierto por
   * "Crear invitación", nunca a la vez que el modal de compartir. */
  permissionStep = signal(false);
  /** Paso 2 del enlace público: contraseña + descarga del original, los dos
   * opcionales. Mismo patrón que permissionStep -- "Compartir álbum" se
   * queda "abierto" en estado detrás. */
  linkOptionsStep = signal(false);
  creatingLink = signal(false);
  /** Qué pestaña del diálogo "Compartir álbum" está activa. */
  shareTab = signal<ShareTab>('new');
  /** Id del enlace cuya contraseña se está reeditando en esa lista; null =
   * ninguno. */
  editingLinkPassword = signal<number | null>(null);
  linkPasswordDraft = '';
  /** Share que se está reeditando desde la lista; null = invitación nueva. */
  editingShare = signal<Share | null>(null);
  savingPermissions = signal(false);
  /** Marcadas en el modal de permisos. Las de lectura no están aquí: van
   * siempre incluidas y el modal las pinta fijas. */
  draftCapabilities = signal<ReadonlySet<AlbumCapability>>(new Set(ALBUM_CAPABILITIES));

  /** Ids marcados en la galería. Vacío significa que no hay selección activa. */
  selection = signal<ReadonlySet<number>>(new Set<number>());
  bulkRunning = signal(false);

  closeSharing = () => {
    this.sharing.set(false);
    this.sharePassword = '';
    this.shareAllowOriginalDownload = false;
    this.shareShowMetadata = true;
    this.editingLinkPassword.set(null);
    this.linkPasswordDraft = '';
    this.hideQr();
  };
  closeEditing = () => this.editing.set(false);
  closeActivity = () => this.showingActivity.set(false);
  closeTags = () => this.selectedMedia.set(null);
  closePermissionStep = () => { if (!this.savingPermissions()) { this.permissionStep.set(false); this.editingShare.set(null); } };
  closeLinkOptionsStep = () => { if (!this.creatingLink()) { this.linkOptionsStep.set(false); this.sharePassword = ''; this.shareAllowOriginalDownload = false; this.shareShowMetadata = true; } };

  permission: SharePermission = 'read';
  expiresAt = '';
  /** Exclusivos del enlace público -- se resetean al cerrar el diálogo de
   * compartir, igual que `expiresAt`. */
  sharePassword = '';
  shareAllowOriginalDownload = false;
  shareShowMetadata = true;
  editTitle = '';
  editDescription = '';
  editPrivate = true;
  filterType = '';
  filterTag = '';
  filterFavorite = '';
  filterPlace = '';
  sortKey: SortKey = 'created_at:desc';
  tagToAssign = '';
  newTagName = '';

  readonly sortOptions = Object.entries(SORT_OPTIONS).map(([value, option]) => ({ value, label: option.label }));

  private resolveBackLink() {
    const params = this.route.snapshot.queryParamMap;
    const username = params.get('username');
    if (params.get('from') === 'profile' && username) {
      return { label: `Volver al perfil de ${username}`, path: `/u/${username}` };
    }
    return ORIGINS[params.get('from') as keyof typeof ORIGINS] ?? { label: 'Volver a álbumes', path: '/albumes' };
  }

  ngOnInit() {
    this.loadAlbum();
    this.loadMedia();
    this.api.tags('', this.id).subscribe({
      next: r => this.tags.set(r.data),
      error: error => this.error.set(error?.error?.message || 'No se pudieron cargar los tags.'),
    });
  }

  /** Compartir, papelera, desactivar/borrar el álbum: exclusivo del dueño. */
  canManage() { return canManageAlbum(this.album()?.role); }
  canUpload() { return canUploadMedia(this.album()?.role, this.album()?.capabilities); }
  canDelete() { return canDeleteMedia(this.album()?.role, this.album()?.capabilities); }
  canOrganize() { return canOrganizeMedia(this.album()?.role, this.album()?.capabilities); }
  canEdit() { return canEditAlbum(this.album()?.role, this.album()?.capabilities); }

  loadAlbum() {
    this.error.set('');
    this.api.album(this.id).subscribe({
      next: response => {
        const album = response.data;
        this.album.set(album);
        this.editTitle = album.titulo;
        this.editDescription = album.descripcion || '';
        this.editPrivate = album.is_private;
        if (album.role === 'owner') this.loadShares();
      },
      error: error => this.error.set(error?.error?.message || 'No se pudo cargar el álbum.'),
    });
  }

  loadMedia() {
    this.error.set('');
    this.pages.reset();
  }


  loadShares() {
    this.api.shares(this.id).subscribe({
      next: r => this.shares.set(r.data),
      error: error => this.error.set(error?.error?.message || 'No se pudieron cargar los accesos.'),
    });
  }

  onUploaded() {
    this.uploadedInPanel++;
  }

  openUpload() {
    this.uploadedInPanel = 0;
    this.uploading.set(true);
  }

  closeUpload() {
    this.uploading.set(false);
    const count = this.uploadedInPanel;
    this.uploadedInPanel = 0;
    // Un corte/5xx puede ocultar una subida ya confirmada. Al cerrar se fuerza
    // una lectura real para que "revisa el álbum" pueda reconciliar la UI sin
    // repetir a ciegas el POST y crear un duplicado.
    this.api.invalidate();
    this.loadMedia();
    if (count) this.toast.success(`${count} archivo${count === 1 ? '' : 's'} subido${count === 1 ? '' : 's'}.`);
  }

  /** Zip del album entero. Mismo nivel que verlo: quien ya puede ver cada
   * foto ya podia guardarla una por una con el boton del detalle. */
  downloadAlbum() {
    if (this.downloadingAlbum()) return;
    this.downloadingAlbum.set(true);
    this.api.downloadAlbum(this.id).subscribe({
      next: blob => {
        this.downloadingAlbum.set(false);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${this.album()?.titulo || 'album'}.zip`;
        a.click();
        URL.revokeObjectURL(url);
      },
      error: error => {
        this.downloadingAlbum.set(false);
        this.toast.error(error?.error?.message || 'No se pudo descargar el álbum.');
      },
    });
  }

  // --- Selección múltiple -------------------------------------------------

  toggleSelection(item: Media) {
    this.selection.update(current => {
      const next = new Set(current);
      next.has(item.id) ? next.delete(item.id) : next.add(item.id);
      return next;
    });
  }

  clearSelection() { this.selection.set(new Set<number>()); }

  /**
   * Las acciones por lote recorren los endpoints unitarios que ya existen. Una
   * selección de galería son unos pocos elementos, así que un endpoint nuevo
   * por lote sería código que hoy nadie necesita.
   */
  favoriteSelected() {
    const ids = [...this.selection()];
    if (!ids.length || !this.canOrganize()) return;

    this.bulkRunning.set(true);
    forkJoin(ids.map(id => this.api.favorite(id, true))).subscribe({
      next: () => {
        this.bulkRunning.set(false);
        this.clearSelection();
        this.toast.success(ids.length === 1 ? 'Añadido a favoritos.' : `${ids.length} archivos añadidos a favoritos.`);
        this.loadMedia();
      },
      error: error => {
        this.bulkRunning.set(false);
        this.toast.error(error?.error?.message || 'No se pudieron marcar como favoritos.');
        this.loadMedia();
      },
    });
  }

  archiveSelected() {
    const ids = [...this.selection()];
    if (!ids.length || !this.canOrganize()) return;

    this.bulkRunning.set(true);
    forkJoin(ids.map(id => this.api.archive(id, true))).subscribe({
      next: () => {
        this.bulkRunning.set(false);
        this.clearSelection();
        this.toast.success(ids.length === 1 ? 'Archivado.' : `${ids.length} archivos archivados.`);
        this.loadMedia();
      },
      error: error => {
        this.bulkRunning.set(false);
        this.toast.error(error?.error?.message || 'No se pudieron archivar.');
        this.loadMedia();
      },
    });
  }

  async deleteSelected() {
    const ids = [...this.selection()];
    if (!ids.length) return;

    const confirmed = await this.confirm.ask({
      title: 'Enviar a la papelera',
      message: ids.length === 1
        ? 'El archivo irá a la papelera del dueño del álbum y podrá recuperarse durante 30 días.'
        : `Los ${ids.length} archivos irán a la papelera del dueño del álbum y podrán recuperarse durante 30 días.`,
      confirmLabel: 'Enviar a la papelera',
      danger: true,
    });
    if (!confirmed) return;

    this.bulkRunning.set(true);
    forkJoin(ids.map(id => this.api.deleteMedia(id))).subscribe({
      next: () => {
        this.bulkRunning.set(false);
        this.clearSelection();
        this.toast.success(ids.length === 1 ? 'Archivo enviado a la papelera.' : `${ids.length} archivos enviados a la papelera.`);
        this.loadMedia();
      },
      error: error => {
        this.bulkRunning.set(false);
        this.toast.error(error?.error?.message || 'No se pudieron eliminar los archivos.');
        this.loadMedia();
      },
    });
  }

  // --- Ajustes del álbum --------------------------------------------------

  saveSettings() {
    if (!this.canEdit() || this.savingSettings()) return;
    this.savingSettings.set(true);
    this.api.updateAlbum(this.id, {
      titulo: this.editTitle,
      descripcion: this.editDescription || null,
      is_private: this.editPrivate,
    }).subscribe({
      next: response => {
        this.album.set(response.data);
        this.savingSettings.set(false);
        this.editing.set(false);
        this.toast.success('Álbum actualizado.');
      },
      error: error => {
        this.savingSettings.set(false);
        this.toast.error(error?.error?.message || 'No se pudo actualizar el álbum.');
      },
    });
  }

  async deleteAlbum() {
    if (!this.canManage()) return;
    const confirmed = await this.confirm.ask({
      title: 'Eliminar álbum',
      message: 'Se eliminará este álbum de forma permanente: no pasa por la papelera y no se podrá recuperar. Sus fotos y videos no se borran, siguen en tu biblioteca.',
      confirmLabel: 'Eliminar álbum',
      danger: true,
    });
    if (!confirmed) return;

    this.api.deleteAlbum(this.id).subscribe({
      next: () => {
        this.toast.success('Álbum eliminado.');
        this.router.navigateByUrl('/albumes');
      },
      error: error => this.toast.error(error?.error?.message || 'No se pudo eliminar el álbum.'),
    });
  }

  // --- Acciones por archivo ----------------------------------------------

  async deleteMedia(item: Media) {
    const confirmed = await this.confirm.ask({
      title: 'Enviar a la papelera',
      message: 'El archivo irá a la papelera del dueño del álbum y podrá recuperarse durante 30 días.',
      confirmLabel: 'Enviar a la papelera',
      danger: true,
    });
    if (!confirmed) return;

    this.api.deleteMedia(item.id).subscribe({
      next: () => { this.toast.success('Archivo enviado a la papelera.'); this.loadMedia(); },
      error: error => this.toast.error(error?.error?.message || 'No se pudo eliminar el archivo.'),
    });
  }

  toggleFavorite(item: Media) {
    if (!this.canOrganize()) return;
    this.api.favorite(item.id, !item.is_favorite).subscribe({
      next: response => this.media.update(items => items.map(m => m.id === item.id ? { ...m, is_favorite: response.data.is_favorite } : m)),
      error: error => this.toast.error(error?.error?.message || 'No se pudo actualizar el favorito.'),
    });
  }

  setCover(item: Media) {
    if (!this.canEdit()) return;
    this.api.updateAlbum(this.id, { cover_media_id: item.id }).subscribe({
      next: response => { this.album.set(response.data); this.toast.success('Portada actualizada.'); },
      error: error => this.toast.error(error?.error?.message || 'No se pudo cambiar la portada.'),
    });
  }

  clearCover() {
    if (!this.canEdit()) return;
    this.api.updateAlbum(this.id, { cover_media_id: null }).subscribe({
      next: response => { this.album.set(response.data); this.toast.success('Portada quitada.'); },
      error: error => this.toast.error(error?.error?.message || 'No se pudo quitar la portada.'),
    });
  }

  // --- Tags ---------------------------------------------------------------

  openTags(item: Media) {
    if (!this.canOrganize()) return;
    this.selectedMedia.set(item);
    this.api.mediaDetail(item.id).subscribe({
      next: r => this.selectedMediaTags.set(r.data.tags),
      error: error => this.toast.error(error?.error?.message || 'No se pudieron cargar los tags.'),
    });
  }

  assignTag() {
    const item = this.selectedMedia();
    const tagId = Number(this.tagToAssign);
    if (!item || !tagId) return;
    this.api.assignTags(item.id, [tagId]).subscribe({
      next: () => {
        const tag = this.tags().find(t => t.id === tagId);
        if (tag) this.selectedMediaTags.update(tags => upsertTagSorted(tags, tag));
        this.tagToAssign = '';
      },
      error: error => this.toast.error(error?.error?.message || 'No se pudo asignar el tag.'),
    });
  }

  createAndAssignTag() {
    const name = this.newTagName.trim();
    const item = this.selectedMedia();
    if (!name || !item) return;
    this.api.createTag(name, this.id).subscribe({
      next: response => {
        const tag = response.data;
        this.tags.update(tags => upsertTagSorted(tags, tag));
        this.api.assignTags(item.id, [tag.id]).subscribe({
          next: () => {
            this.selectedMediaTags.update(tags => upsertTagSorted(tags, tag));
            this.newTagName = '';
          },
          error: error => this.toast.error(error?.error?.message || 'No se pudo asignar el tag.'),
        });
      },
      error: error => this.toast.error(error?.error?.message || 'No se pudo crear el tag.'),
    });
  }

  removeTag(tag: Tag) {
    const item = this.selectedMedia();
    if (!item) return;
    this.api.unassignTag(item.id, tag.id).subscribe({
      next: () => this.selectedMediaTags.update(tags => tags.filter(t => t.id !== tag.id)),
      error: error => this.toast.error(error?.error?.message || 'No se pudo quitar el tag.'),
    });
  }

  // --- Compartir ----------------------------------------------------------

  /** Abre el diálogo en la pestaña más útil: si ya hay colaboradores o
   * enlaces, en "quién tiene acceso" (lo que se consulta más veces después
   * de la primera); si el álbum no se ha compartido nunca, en "Nuevo
   * acceso", que es lo único que hay que hacer todavía. */
  openSharing() {
    if (!this.canManage()) return;
    this.shareTab.set(
      this.collaboratorShares().length ? 'collaborators'
      : this.publicLinkShares().length ? 'links'
      : 'new'
    );
    this.sharing.set(true);
  }

  setShareTab(tab: ShareTab) {
    this.shareTab.set(tab);
    // Un QR abierto en una pestaña no tiene sentido al cambiar a otra.
    this.hideQr();
  }

  /** Paso 1 -> paso 2 del enlace público: crear el enlace en sí espera a
   * confirmPublicLink(), para que la contraseña/descarga sean de verdad
   * saltables en vez de un desplegable dentro del mismo paso. */
  openLinkOptionsStep() {
    if (!this.canManage() || this.permission !== 'read') return;
    this.linkOptionsStep.set(true);
  }

  confirmPublicLink() {
    if (this.creatingLink()) return;
    this.creatingLink.set(true);
    this.api.createShare(this.id, {
      create_link: true,
      share_type: 'public_link',
      permission: 'read',
      expires_at: this.expiresAt || null,
      ...publicLinkOptions(this.sharePassword, this.shareAllowOriginalDownload, this.shareShowMetadata),
    }).subscribe({
      next: response => {
        this.creatingLink.set(false);
        this.linkOptionsStep.set(false);
        this.sharePassword = '';
        this.shareAllowOriginalDownload = false;
        this.shareShowMetadata = true;
        if (response.data.token) this.copyShareToken(response.data.token, 'public_link', response.data.id);
        this.loadShares();
      },
      error: error => {
        this.creatingLink.set(false);
        this.toast.error(error?.error?.message || 'No se pudo crear el enlace.');
      },
    });
  }

  /** Solo lectura crea el acceso de cuenta directo; un colaborador pasa
   * antes por el modal de permisos, porque no hay un "colaborador" genérico
   * que asumir. */
  createAccountLink() {
    if (!this.canManage()) return;
    if (this.permission === 'read') return this.createAccountReadLink();
    this.editingShare.set(null);
    this.draftCapabilities.set(new Set(ALBUM_CAPABILITIES));
    this.permissionStep.set(true);
  }

  toggleDraftCapability(capability: AlbumCapability) {
    this.draftCapabilities.update(current => {
      const next = new Set(current);
      next.has(capability) ? next.delete(capability) : next.add(capability);
      return next;
    });
  }

  isDraftCapability(capability: AlbumCapability) { return this.draftCapabilities().has(capability); }

  /** Guarda el paso de permisos: crea la invitación nueva, o reescribe las de
   * un colaborador que ya tenía acceso. */
  confirmPermissions() {
    const chosen = ALBUM_CAPABILITIES.filter(c => this.draftCapabilities().has(c));
    if (!chosen.length || this.savingPermissions()) return;
    this.savingPermissions.set(true);

    const existing = this.editingShare();
    const request = existing
      ? this.api.updateSharePermission(existing.id, 'write', chosen)
      : this.api.createShare(this.id, { create_link: true, share_type: 'account', permission: 'write', capabilities: chosen, expires_at: this.expiresAt || null });

    request.subscribe({
      next: response => {
        this.savingPermissions.set(false);
        this.permissionStep.set(false);
        this.editingShare.set(null);
        if (!existing && response.data.token) this.copyShareToken(response.data.token, 'account', response.data.id);
        else this.toast.success('Permisos actualizados.');
        this.loadShares();
      },
      error: error => {
        this.savingPermissions.set(false);
        this.toast.error(error?.error?.message || 'No se pudieron guardar los permisos.');
      },
    });
  }

  /** Reabre el mismo modal de permisos sobre un colaborador ya invitado. */
  /** Se abre COMO PASO 2 sobre "Compartir álbum", que se queda abierto detrás
   * -- igual que ya hacía crear un colaborador nuevo. Antes cerraba el
   * diálogo entero, así que guardar los permisos dejaba a quien editaba sin
   * ver la lista actualizada hasta volver a abrir "Compartir" a mano. */
  editSharePermissions(share: Share) {
    if (!this.canManage()) return;
    this.editingShare.set(share);
    this.draftCapabilities.set(new Set(share.capabilities || []));
    this.permissionStep.set(true);
  }

  private createAccountReadLink() {
    this.api.createShare(this.id, {
      create_link: true,
      share_type: 'account',
      permission: this.permission,
      expires_at: this.expiresAt || null,
    }).subscribe({
      next: response => {
        if (response.data.token) this.copyShareToken(response.data.token, 'account', response.data.id);
        this.loadShares();
      },
      error: error => this.toast.error(error?.error?.message || 'No se pudo crear el acceso.'),
    });
  }

  /** El QR se dibuja en el navegador: el enlace nunca sale hacia un
   * servicio de QR, porque mandarlo sería entregar el token del álbum. */
  readonly qrLink = signal('');

  toggleQr(url: string) {
    this.qrLink.set(this.qrLink() === url ? '' : url);
  }

  /** Nombre del archivo descargado: el título del álbum, nunca el token. */
  qrFilename() {
    return `qr-${this.album()?.titulo || 'album'}`;
  }

  shareUrl(share: Share) {
    if (!share.token) return '';
    return `${location.origin}/${share.share_type === 'public_link' ? 'enlace' : 'invitacion'}/${share.token}`;
  }

  copyCurrentLink() {
    const url = this.copiedLink();
    if (url) void this.copyText(url);
  }

  /** Si el portapapeles se niega, el enlace sigue a la vista en el diálogo
   * para copiarlo a mano (F06: antes quedaba un NotAllowedError sin capturar). */
  async copyText(url: string) {
    if (await copyToClipboard(url)) this.toast.success('Enlace copiado al portapapeles.');
    else this.toast.error('No se pudo copiar automáticamente. Selecciona el enlace y cópialo a mano.');
  }

  private hideQr() { this.qrLink.set(''); }

  copyShare(share: Share) {
    if (!share.token) return;
    this.copyShareToken(share.token, share.share_type || 'account');
  }

  private copyShareToken(token: string, shareType: 'account' | 'public_link', shareId?: number) {
    const url = this.buildShareUrl(token, shareType);
    this.copiedLink.set(url);
    if (shareId) this.rememberToken(shareId, token);
    void this.copyText(url);
  }

  private buildShareUrl(token: string, shareType: 'account' | 'public_link') {
    return `${location.origin}/${shareType === 'public_link' ? 'enlace' : 'invitacion'}/${token}`;
  }

  /**
   * Tokens conocidos EN ESTA SESION, por id de compartición.
   *
   * La base solo guarda `sha256(token)` desde S08, así que el listado nunca
   * puede devolver el enlace: sin esto, copiar o sacar el QR de un acceso ya
   * creado sería imposible sin regenerarlo. Vive en memoria a propósito --
   * guardarlo en el navegador sería volver a tener el secreto en un sitio
   * que un XSS puede leer, justo lo que S01/S08 quitaron.
   */
  readonly linkTokens = signal<Record<number, string>>({});
  private rememberToken(shareId: number, token: string) {
    this.linkTokens.update(current => ({ ...current, [shareId]: token }));
  }

  /** El enlace de esta fila, si se conoce en esta sesión. */
  knownShareUrl(share: Share) {
    const token = this.linkTokens()[share.id];
    return token ? this.buildShareUrl(token, share.share_type || 'account') : '';
  }

  copyKnownLink(share: Share) {
    const url = this.knownShareUrl(share);
    if (!url) return;
    void this.copyText(url);
  }

  readonly revealingShareId = signal<number | null>(null);

  /** Descifra el token de un enlace ya creado (`share.can_reveal`, cifrado
   * reversible desde esta fase) y lo deja en `linkTokens` -- desde ahí
   * Copiar/Ver QR funcionan igual que justo después de crearlo. No lo copia
   * solo: "Ver enlace" revela, "Copiar enlace" copia, cada botón hace una
   * cosa. */
  revealLink(share: Share) {
    if (this.revealingShareId() !== null) return;
    this.revealingShareId.set(share.id);
    this.api.revealShareLink(share.id).subscribe({
      next: response => {
        this.revealingShareId.set(null);
        this.rememberToken(share.id, response.data.token);
      },
      error: error => {
        this.revealingShareId.set(null);
        this.toast.error(error?.error?.message || 'No se pudo mostrar el enlace.');
      },
    });
  }

  /**
   * Emite un token NUEVO e invalida el anterior -- para cuando el enlace se
   * filtró y hace falta matarlo sin dejar de compartir el álbum. Sigue
   * existiendo aunque el token ya se pueda ver: son dos acciones distintas,
   * "enséñamelo otra vez" y "que deje de servir el que ya di".
   */
  async regenerateLink(share: Share) {
    if (!this.canManage()) return;
    const confirmed = await this.confirm.ask({
      title: 'Regenerar enlace',
      message: 'Se creará un enlace nuevo y el anterior dejará de funcionar al instante. '
        + 'Quien ya lo tuviera tendrá que recibir el nuevo.',
      confirmLabel: 'Regenerar',
      danger: true,
    });
    if (!confirmed) return;
    this.api.regenerateShareLink(share.id).subscribe({
      next: response => {
        this.rememberToken(share.id, response.data.token);
        this.copyShareToken(response.data.token, response.data.share_type, share.id);
        this.loadShares();
      },
      error: error => this.toast.error(error?.error?.message || 'No se pudo regenerar el enlace.'),
    });
  }

  readonly capabilityList = ALBUM_CAPABILITIES;
  readonly capabilityLabel = (capability: AlbumCapability) => CAPABILITY_LABELS[capability];
  readonly capabilityHint = (capability: AlbumCapability) => CAPABILITY_HINTS[capability];

  shareLabel(share: Share) {
    if (share.shared_with_username) return `@${share.shared_with_username}`;
    if (share.share_type === 'public_link') return 'Enlace público de solo lectura';
    if (share.token) return `Invitación de ${PERMISSION_LABELS[share.permission].toLowerCase()} pendiente`;
    return 'Acceso de cuenta';
  }

  /** Lo que esta persona puede hacer, en una línea. Se enumera en vez de
   * resumirlo con un nombre de perfil, porque con capacidades sueltas el
   * nombre ya no dice qué concediste. */
  shareDescription(share: Share) {
    if (share.share_type === 'public_link') return 'Se abre sin iniciar sesión y no se guarda en ninguna cuenta';
    if (share.permission === 'read') return 'Solo puede ver las fotos y videos';
    const granted = ALBUM_CAPABILITIES.filter(c => share.capabilities?.includes(c));
    if (granted.length === ALBUM_CAPABILITIES.length) return 'Puede hacer todo menos compartir y borrar el álbum';
    return `Ver · ${granted.map(c => CAPABILITY_LABELS[c].toLowerCase()).join(' · ')}`;
  }

  /** "Colaboradores" es solo quien puede ESCRIBIR. Un acceso de cuenta de
   * solo lectura no es un colaborador: se creó como enlace y se gestiona
   * junto a los demás enlaces. */
  readonly collaboratorShares = computed(() =>
    this.shares().filter(s => s.share_type !== 'public_link' && s.permission === 'write'));

  /** Todos los accesos que se repartieron COMO ENLACE, de los dos tipos: el
   * anónimo (`public_link`) y el que pide iniciar sesión y queda guardado en
   * una cuenta (`account` de solo lectura). Se gestionan juntos porque lo que
   * el dueño quiere hacer con ellos es lo mismo: ver quién lo abrió o en qué
   * cuenta quedó, copiar el enlace, sacar su QR y revocarlo. */
  readonly publicLinkShares = computed(() =>
    this.shares().filter(s => s.share_type === 'public_link' || s.permission === 'read'));

  /** Insignia del botón "Compartir" de la barra: cuántos accesos activos hay
   * en total, de los dos tipos juntos -- antes eran dos insignias, una por
   * botón. */
  readonly totalShareCount = computed(() => this.collaboratorShares().length + this.publicLinkShares().length);

  /** Un enlace anónimo; los ajustes de contraseña/descarga solo aplican ahí. */
  isPublicLink(share: Share) { return share.share_type === 'public_link'; }

  /** En qué cuenta quedó guardado, cuando aplica. */
  savedInAccount(share: Share) {
    if (share.share_type === 'public_link') return '';
    if (share.shared_with_username) return `@${share.shared_with_username}`;
    return share.has_token ? 'Todavía sin reclamar' : '';
  }

  /** "Permitir descargar el original" de un enlace YA CREADO -- se guarda
   * al instante, sin un botón de guardar aparte (mismo criterio que
   * toggleFavorite en la galería). */
  toggleLinkDownload(share: Share) {
    const next = !share.allow_original_download;
    this.api.updateSharePermission(share.id, 'read', [], { allow_original_download: next }).subscribe({
      next: () => { this.toast.success(next ? 'Ahora se puede descargar el original.' : 'Ya no se puede descargar el original.'); this.loadShares(); },
      error: error => this.toast.error(error?.error?.message || 'No se pudo actualizar el enlace.'),
    });
  }

  startEditLinkPassword(share: Share) {
    this.editingLinkPassword.set(share.id);
    this.linkPasswordDraft = '';
  }

  cancelEditLinkPassword() {
    this.editingLinkPassword.set(null);
    this.linkPasswordDraft = '';
  }

  /** La contraseña ya guardada NUNCA se puede volver a mostrar (va
   * hasheada) -- esto pone una nueva, o la quita si se deja en blanco. */
  saveLinkPassword(share: Share) {
    const password = this.linkPasswordDraft.trim() || null;
    this.api.updateSharePermission(share.id, 'read', [], { password }).subscribe({
      next: () => {
        this.editingLinkPassword.set(null);
        this.linkPasswordDraft = '';
        this.toast.success(password ? 'Contraseña actualizada.' : 'Contraseña quitada.');
        this.loadShares();
      },
      error: error => this.toast.error(error?.error?.message || 'No se pudo actualizar la contraseña.'),
    });
  }

  async revokeShare(share: Share) {
    if (!share.active) return;
    const who = share.share_type === 'public_link' ? 'Cualquiera con este enlace'
      : share.shared_with_username ? `@${share.shared_with_username}` : 'Esta invitación';
    const confirmed = await this.confirm.ask({
      title: 'Revocar acceso',
      message: `${who} dejará de ver este álbum. Las fotos que haya subido se quedan en el álbum.`,
      confirmLabel: 'Revocar acceso',
      danger: true,
    });
    if (!confirmed) return;
    this.api.disableShare(share.id).subscribe({
      next: () => { this.hideQr(); this.loadShares(); this.toast.success('Acceso revocado.'); },
      error: error => this.toast.error(error?.error?.message || 'No se pudo revocar el acceso.'),
    });
  }
}
