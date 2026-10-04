import { Component, input } from '@angular/core';
import { ReactiveFormsModule } from '@angular/forms';
import { Album, Tag } from '../../../core/services/album-api';
import { AdvancedSearchForm } from '../../../core/utils/advanced-search';

/**
 * Los controles de la búsqueda avanzada (todo menos el texto). Uno solo para
 * la búsqueda de /albumes y el editor de álbumes inteligentes: si alguno
 * cambia, cambian los dos, y un álbum inteligente nunca puede guardar un
 * criterio que la búsqueda no entienda. El orden solo tiene sentido al buscar
 * (`showSort`): no forma parte de lo que un álbum inteligente guarda.
 */
@Component({
  selector: 'app-search-filter-fields',
  imports: [ReactiveFormsModule],
  templateUrl: './search-filter-fields.html',
  styleUrl: './search-filter-fields.css',
})
export class SearchFilterFields {
  form = input.required<AdvancedSearchForm>();
  /** Solo álbumes normales del dueño: un álbum inteligente no es un criterio. */
  albums = input<Album[]>([]);
  tags = input<Tag[]>([]);
  showSort = input(true);
  divided = input(true);
  panelId = input('advanced-search-filters');
}
