import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PublicNav } from '../../components/layout/public-nav/public-nav';
import { PublicFooter } from '../../components/layout/public-footer/public-footer';
import { PublicBackdrop } from '../../components/ui/public-backdrop/public-backdrop';
import { ScrollReveal } from '../../core/directives/scroll-reveal';

@Component({
  selector: 'app-about',
  imports: [PublicBackdrop, RouterLink, PublicNav, PublicFooter, ScrollReveal],
  templateUrl: './about.html',
  styleUrl: './about.css',
})
export class About {}
