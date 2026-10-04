import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PublicNav } from '../../components/layout/public-nav/public-nav';
import { PublicFooter } from '../../components/layout/public-footer/public-footer';
import { ShotFrame } from '../../components/ui/shot-frame/shot-frame';
import { PublicBackdrop } from '../../components/ui/public-backdrop/public-backdrop';
import { ScrollReveal } from '../../core/directives/scroll-reveal';

@Component({
  selector: 'app-landing',
  imports: [PublicBackdrop, RouterLink, PublicNav, PublicFooter, ShotFrame, ScrollReveal],
  templateUrl: './landing.html',
  styleUrl: './landing.css',
})
export class Landing {}
