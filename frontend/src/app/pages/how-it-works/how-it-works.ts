import { AfterViewInit, Component, ElementRef, OnDestroy, signal, viewChildren } from '@angular/core';
import { PublicNav } from '../../components/layout/public-nav/public-nav';
import { PublicFooter } from '../../components/layout/public-footer/public-footer';
import { ShotFrame } from '../../components/ui/shot-frame/shot-frame';
import { PublicBackdrop } from '../../components/ui/public-backdrop/public-backdrop';
import { ScrollReveal } from '../../core/directives/scroll-reveal';

/**
 * Las vistas privadas del sistema, capturadas en modo oscuro de la aplicación
 * en marcha (con cuentas y fotos de demostración) y agrupadas en los seis
 * capítulos en que se usa.
 *
 * Dentro de cada capítulo no pesan lo mismo: una captura es la que explica el
 * capítulo (`featured`) y va grande, y las otras tres lo completan (`supporting`)
 * en una cuadrícula compacta. Capturas del mismo tamaño, una detrás de otra,
 * eran una página en la que ninguna destacaba.
 */
const STAGES = [
  {
    number: '01',
    label: 'Tu inicio y tus álbumes',
    featured: {
      src: 'capturas/paso-albumes.webp', zoom: 1,
      titulo: 'Mis álbumes',
      texto: 'Un álbum por historia, cada uno con su portada. La etiqueta Privado dice de un vistazo quién puede verlo, y debajo viven tus álbumes inteligentes.',
      alt: 'Cuadrícula de álbumes propios, cada uno con su portada y su estado',
    },
    supporting: [
      {
        src: 'capturas/paso-inicio.webp', zoom: 1,
        titulo: 'Inicio',
        texto: 'Lo que pasó un día como hoy en otros años, las fotos que otras personas hacen públicas y quién más guarda recuerdos aquí.',
        alt: 'La página de Inicio con recuerdos de este día y el muro de fotos públicas',
      },
      {
        src: 'capturas/paso-album-detalle.webp', zoom: 1,
        titulo: 'Dentro de un álbum',
        texto: 'Filtras por tipo, etiqueta, lugar o favoritos, y tienes a mano agregar, compartir, la actividad y los ajustes.',
        alt: 'Un álbum abierto con sus fotos, sus filtros y sus acciones',
      },
      {
        src: 'capturas/paso-subida.webp', zoom: 1.35,
        titulo: 'Subir fotos y videos',
        texto: 'Arrastras uno o varios archivos y cada uno sube con su propio progreso. Si ya tenías esa misma foto, AlbumFP te lo dice antes de duplicarla.',
        alt: 'Panel para agregar fotos o videos a un álbum',
      },
    ],
  },
  {
    number: '02',
    label: 'Cada recuerdo cuenta su historia',
    featured: {
      src: 'capturas/paso-recuerdo.webp', zoom: 1,
      titulo: 'Contexto del recuerdo',
      texto: 'El detalle muestra la fecha y las coordenadas que ya trae la foto. Esta edición local no consulta mapas, clima ni otros servicios en línea.',
      alt: 'Detalle local de una foto con la fecha y los datos disponibles',
    },
    supporting: [
      {
        src: 'capturas/paso-comentarios.webp', zoom: 1,
        titulo: 'Comentarios',
        texto: 'Quien tiene cuenta y puede ver la foto puede comentarla. Cada quien edita o borra solo lo suyo; con un enlace público se leen, pero no se escriben.',
        alt: 'Conversación de comentarios debajo de una foto, con el cuadro para escribir uno nuevo',
      },
      {
        src: 'capturas/paso-ficha.webp', zoom: 1,
        titulo: 'La ficha del archivo',
        texto: 'La ficha reúne los datos disponibles en el archivo, como formato, tamaño y metadatos de cámara o fecha cuando existan.',
        alt: 'Ficha técnica de una foto con los datos disponibles en el archivo',
      },
      {
        src: 'capturas/paso-lugares.webp', zoom: 1,
        titulo: 'Lugares',
        texto: 'Agrupa recuerdos cuando el archivo ya incluye un lugar reconocible. La búsqueda de direcciones en línea está desactivada en esta edición local.',
        alt: 'Vista local de lugares agrupados a partir de los datos disponibles en las fotos',
      },
    ],
  },
  {
    number: '03',
    label: 'Ordena y encuentra',
    featured: {
      src: 'capturas/paso-biblioteca.webp', zoom: 1,
      titulo: 'Biblioteca',
      texto: 'Todas tus fotos y videos en una sola línea de tiempo, por año y por mes, estén en un álbum, en varios o en ninguno.',
      alt: 'La Biblioteca con los añadidos recientes y las fotos agrupadas por mes',
    },
    supporting: [
      {
        src: 'capturas/paso-busqueda.webp', zoom: 1,
        titulo: 'Buscar',
        texto: 'Busca en títulos, descripciones, etiquetas, nombres de archivo y datos disponibles, con filtros por fecha, tipo o álbum. El OCR no está activo en esta edición local.',
        alt: 'Resultados de una búsqueda con sus filtros',
      },
      {
        src: 'capturas/paso-etiquetas.webp', zoom: 1,
        titulo: 'Etiquetas',
        texto: 'Las etiquetas las eliges tú. Las sugerencias automáticas de etiquetas están desactivadas en esta edición local.',
        alt: 'Panel de etiquetas de una foto, con las suyas puestas',
      },
      {
        src: 'capturas/paso-texto.webp', zoom: 1,
        titulo: 'Texto de una foto',
        texto: 'La extracción de texto requiere un servicio en línea y está desactivada en esta edición local. Tus fotos no se envían a OCR.',
        alt: 'Detalle local de una foto con la extracción de texto desactivada',
      },
    ],
  },
  {
    number: '04',
    label: 'Favoritos, archivo y papelera',
    featured: {
      src: 'capturas/paso-archivo.webp', zoom: 1,
      titulo: 'Archivo',
      texto: 'Lo que no quieres ver cada día sale de Inicio y de la Biblioteca sin borrarse: sigue en sus álbumes y en las búsquedas.',
      alt: 'La vista Archivo con fotos archivadas',
    },
    supporting: [
      {
        src: 'capturas/paso-favoritos.webp', zoom: 1,
        titulo: 'Favoritos',
        texto: 'El corazón deja lo mejor en una lista propia, reunida de todos tus álbumes.',
        alt: 'Lista de favoritos con fotos de varios álbumes',
      },
      {
        src: 'capturas/paso-inteligente.webp', zoom: 1,
        titulo: 'Álbumes inteligentes',
        texto: 'Una búsqueda guardada con nombre. Se recalcula cada vez que la abres, así que lo nuevo aparece solo.',
        alt: 'Un álbum inteligente con sus filtros y las fotos que cumplen con ellos',
      },
      {
        src: 'capturas/paso-papelera.webp', zoom: 1,
        titulo: 'Papelera',
        texto: 'Las fotos y los videos que borras esperan 30 días. Solo tú ves tu papelera.',
        alt: 'La papelera con archivos y los días que les quedan antes de eliminarse',
      },
    ],
  },
  {
    number: '05',
    label: 'Comparte y mantén el control',
    featured: {
      src: 'capturas/paso-compartir.webp', zoom: 1.35,
      titulo: 'Compartir un álbum',
      texto: 'Solo lectura o colaborador. Enlace público que se abre sin cuenta, o invitación que pide iniciar sesión.',
      alt: 'Diálogo de compartir un álbum con los perfiles de acceso y los tipos de enlace',
    },
    supporting: [
      {
        src: 'capturas/paso-capacidades.webp', zoom: 1.35,
        titulo: 'Permisos del colaborador',
        texto: 'Marcas una por una qué podrá hacer: subir, editar, enviar a la papelera, organizar o cambiar los ajustes.',
        alt: 'Diálogo con las cinco capacidades que se le pueden conceder a un colaborador',
      },
      {
        src: 'capturas/paso-enlaces.webp', zoom: 1.35,
        titulo: 'Enlaces públicos',
        texto: 'Cada enlace puede llevar contraseña y permitir o no descargar el original. Lo revocas cuando quieras y deja de abrir al instante.',
        alt: 'Lista de enlaces públicos de un álbum con sus opciones',
      },
      {
        src: 'capturas/paso-compartido.webp', zoom: 1,
        titulo: 'Compartido conmigo',
        texto: 'Lo que otras personas te abrieron, separado según puedas solo mirar o también colaborar.',
        alt: 'Vista de álbumes compartidos contigo',
      },
    ],
  },
  {
    number: '06',
    label: 'Tu cuenta',
    featured: {
      src: 'capturas/paso-perfil.webp', zoom: 1,
      titulo: 'Mi perfil',
      texto: 'Tus passkeys y tus códigos de recuperación, la exportación de toda tu biblioteca en un ZIP y, arriba, las sesiones que tienes abiertas.',
      alt: 'Mi perfil con las passkeys, los códigos de recuperación y la exportación de la biblioteca',
    },
    supporting: [
      {
        src: 'capturas/paso-avisos.webp', zoom: 1,
        titulo: 'Avisos',
        texto: 'La campana te cuenta cuando te invitan a un álbum o alguien sube a uno compartido. Solo dentro de la aplicación: nada de notificaciones push ni correos.',
        alt: 'La campana de avisos abierta con las últimas novedades',
      },
      {
        src: 'capturas/paso-actividad.webp', zoom: 1.35,
        titulo: 'Actividad del álbum',
        texto: 'Quién subió, añadió, quitó o cambió la portada, visible para ti y para quienes tienen acceso con su cuenta; un enlace público no la ve.',
        alt: 'La actividad reciente de un álbum compartido',
      },
      {
        src: 'capturas/paso-perfil-publico.webp', zoom: 1,
        titulo: 'Perfil público',
        texto: 'Lo que otras personas con cuenta ven de ti: tu nombre, tu foto y tus álbumes públicos. Los privados no aparecen.',
        alt: 'Perfil público de una cuenta con sus álbumes públicos',
      },
    ],
  },
] as const;

const FAQ = [
  {
    p: '¿Un álbum público expone todas mis fotos?',
    r: 'Solo las de ese álbum. Marcarlo como público deja que lo vean las personas con cuenta en AlbumFP, en el muro de Inicio y en tu perfil, pero el resto de tus álbumes sigue siendo privado. Lo que esté en la papelera no lo ve nadie más que tú.',
  },
  {
    p: '¿Qué puede hacer un colaborador?',
    r: 'Lo que tú marques. Al invitarlo eliges una por una cinco cosas: subir archivos, editar sus datos, enviarlos a la papelera, marcar favoritos y etiquetas, y cambiar los ajustes del álbum. Ver siempre va incluido y no se puede quitar. Compartir el álbum con más gente, entrar a su papelera o borrarlo siguen siendo solo tuyos.',
  },
  {
    p: '¿Quién puede comentar una foto?',
    r: 'Cualquiera con cuenta que pueda ver esa foto. Nadie comenta sin cuenta: quien entra por un enlace público lee los comentarios, pero no puede escribir. Cada persona edita o borra solo sus propios comentarios.',
  },
  {
    p: '¿Puedo editar una foto después de subirla?',
    r: 'Puedes corregir su título, su descripción, su fecha y su lugar cuando quieras. El archivo en sí no se toca nunca: AlbumFP guarda el original tal como lo subiste. Si vacías la fecha o el lugar, vuelve a mandar lo que traiga la propia foto.',
  },
  {
    p: '¿De dónde salen el lugar y la fecha de una foto?',
    r: 'AlbumFP conserva la fecha y las coordenadas que vienen en el archivo o que añades al editarlo. Esta Demo no consulta mapas, clima ni servicios externos para resolver esos datos.',
  },
  {
    p: '¿Mis fotos salen del servidor alguna vez?',
    r: 'No. Esta edición local no envía fotos ni datos a servicios externos. La extracción de texto y las sugerencias automáticas están desactivadas.',
  },
  {
    p: '¿Qué pasa si elimino un álbum?',
    r: 'Se borra el álbum, de forma permanente: los álbumes no pasan por la papelera, así que la aplicación te pide confirmarlo antes. Sus fotos y videos no se borran; siguen en tu biblioteca y en tus otros álbumes.',
  },
  {
    p: '¿Puedo llevarme mis fotos?',
    r: 'Sí. Desde Mi perfil descargas un ZIP con todos tus originales, tus álbumes, etiquetas y comentarios. Se genera mientras se descarga y no queda ninguna copia en el servidor.',
  },
  {
    p: '¿Y si olvido mi contraseña?',
    r: 'Como AlbumFP no usa correo, la recuperación depende de ti: genera tus códigos de recuperación en Mi perfil y guárdalos en un lugar seguro, o añade una passkey para entrar sin contraseña. Sin ninguna de las dos no hay forma de recuperar la cuenta.',
  },
  {
    p: '¿Hace falta un correo para registrarse?',
    r: 'No. La cuenta se crea con tu nombre completo, un nombre de usuario y una contraseña. AlbumFP no pide ni almacena correos.',
  },
  {
    p: '¿Funciona sin conexión?',
    r: 'No necesitas internet para usar esta instalación local, pero sí que el equipo donde corre AlbumFP esté encendido. La biblioteca no se descarga automáticamente al teléfono.',
  },
] as const;

@Component({
  selector: 'app-how-it-works',
  imports: [PublicBackdrop, PublicNav, PublicFooter, ShotFrame, ScrollReveal],
  templateUrl: './how-it-works.html',
  styleUrl: './how-it-works.css',
})
export class HowItWorks implements AfterViewInit, OnDestroy {
  readonly stages = STAGES;
  readonly faq = FAQ;
  /** Sale de los datos: escrito a mano en la plantilla se quedo desfasado. */
  readonly totalPantallas = STAGES.reduce((n, e) => n + 1 + e.supporting.length, 0);

  /** Etapa que el carril marca como actual. */
  readonly active = signal(0);

  private readonly sections = viewChildren<ElementRef<HTMLElement>>('stage');
  private observer?: IntersectionObserver;

  ngAfterViewInit() {
    // IntersectionObserver y no un listener de scroll: el navegador solo avisa
    // cuando una etapa cruza la línea del 40%, no en cada píxel. El aviso solo
    // dispara el recálculo; cuál es la etapa activa se decide mirando dónde
    // están todas, porque un salto de scroll puede cruzar una entera y quedarse
    // sin su aviso de entrada.
    this.observer = new IntersectionObserver(() => this.sync(), { rootMargin: '-40% 0px -60% 0px' });
    for (const section of this.sections()) this.observer.observe(section.nativeElement);
    this.sync();
  }

  private sync() {
    const line = innerHeight * 0.4;
    let index = 0;
    this.sections().forEach((section, i) => {
      if (section.nativeElement.getBoundingClientRect().top <= line) index = i;
    });
    this.active.set(index);
  }

  ngOnDestroy() {
    this.observer?.disconnect();
  }
}
